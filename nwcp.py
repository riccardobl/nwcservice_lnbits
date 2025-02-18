import asyncio
import base64
import hashlib
import json
import random
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

import secp256k1
import websockets.client as websockets
from Cryptodome import Random
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad, unpad
from lnbits.helpers import encrypt_internal_message
from lnbits.settings import settings
from loguru import logger
from pydantic import BaseModel


class RateLimit:
    backoff: int = 0
    last_attempt_time: int = 0


class MainSubscription:
    def __init__(self):
        self.requests_sub_id: Optional[str] = None
        self.responses_sub_id: Optional[str] = None
        self.requests_eose = False
        self.responses_eose = False
        self.events: Dict[str, Dict] = {}
        self.responses: List[str] = []

    def get_stale(self) -> List[Dict]:
        """
        Get all the pending events that do not have a response yet.
        """
        pending_events = []
        for [event_id, event] in self.events.items():
            if event_id not in self.responses:
                pending_events.append(event)
        return pending_events

    def register_response(self, event_id: str):
        """
        Register a response for a request event (not stale anymore)
        """
        if event_id not in self.responses:
            self.responses.append(event_id)

    class Config:
        arbitrary_types_allowed = True


class NWCServiceProvider:
    def __init__(self, private_key: Optional[str] = None, relay: Optional[str] = None):
        if not relay:  # Connect to nostrclient
            relay = "nostrclient"
        if relay == "nostrclient":
            relay = f"ws://localhost:{settings.port}/nostrclient/api/v1/relay"
        elif relay == "nostrclient:private":
            relay_endpoint = encrypt_internal_message("relay")
            relay = (
                f"ws://localhost:{settings.port}/nostrclient/api/v1/{relay_endpoint}"
            )
        self.relay = relay

        if not private_key:  # Create random key
            private_key = bytes.hex(secp256k1._gen_private_key())

        self.private_key = secp256k1.PrivateKey(bytes.fromhex(private_key))
        self.private_key_hex = private_key
        self.public_key = self.private_key.pubkey
        if not self.public_key:
            raise Exception("Invalid public key")
        self.public_key_hex = self.public_key.serialize().hex()[2:]

        # List of supported methods
        self.supported_methods: List[str] = []

        # Keep track of the number of subscriptions (used for unique subid)
        self.subscriptions_count: int = 0

        # Request listeners, listen to specific methods
        self.request_listeners: Dict[
            str,
            Callable[
                [NWCServiceProvider, str, Dict],
                Awaitable[List[Tuple[Optional[Dict], Optional[Dict], List]]],
            ],
        ] = {}

        # Reconnect task (if the connection is lost)
        self.reconnect_task = None

        # Subscription
        self.sub = None
        self.rate_limit: Dict[str, RateLimit] = {}

        # websocket connection
        self.ws = None

        # if True the websocket is connected
        self.connected = False

        # if True the instance is shutting down
        self.shutdown = False

        logger.info(
            "NWC Service is ready. relay: "
            + str(self.relay)
            + " pubkey: "
            + self.public_key_hex
        )

    def get_supported_methods(self):
        """
        Returns the list of supported methods by this service provider.
        """
        return self.supported_methods

    def add_request_listener(
        self,
        method: str,
        listener: Callable[
            ["NWCServiceProvider", str, Dict],
            Awaitable[List[Tuple[Optional[Dict], Optional[Dict], List]]],
        ],
    ):
        """
        Adds a request listener for a specific method.

        Args:
            method (str): The method name.
            listener (Callable[
                ["NWCServiceProvider", str, Dict], List[Tuple[Dict, Dict]]
                ]): The listener function
        """
        if method not in self.supported_methods:
            self.supported_methods.append(method)
        self.request_listeners[method] = listener

    async def start(self):
        """
        Starts the NWC service provider.
        """
        self.reconnect_task = asyncio.create_task(self._connect_to_relay())

    def _json_dumps(self, data: Union[Dict, list]) -> str:
        """
        Converts a Python dictionary to a JSON string with compact encoding.

        Args:
            data (Dict): The dictionary to be converted.

        Returns:
            str: The compact JSON string.
        """
        if isinstance(data, Dict):
            data = {k: v for k, v in data.items() if v is not None}
        return json.dumps(data, separators=(",", ":"), ensure_ascii=False)

    def _is_shutting_down(self) -> bool:
        """
        Returns True if the instance is shutting down.
        """
        return self.shutdown or not settings.lnbits_running

    async def _send(self, data: List[Any]):
        """
        Sends data to the relay.

        Args:
            data (Dict): The data to be sent.
        """
        if not self.ws:
            raise Exception("Websocket connection is not established")
        if self._is_shutting_down():
            logger.warning("Trying to send data while shutting down")
            return
        await self._wait_for_connection()  # ensure the connection is established
        tx = self._json_dumps(data)
        await self.ws.send(tx)

    def _get_new_subid(self) -> str:
        """
        Generates a unique subscription id.

        Returns:
            str: The generated 64 characters long subscription id (eg. lnbits0abc...)
        """
        subid = "lnbitsnwcs" + str(self.subscriptions_count)
        self.subscriptions_count += 1
        max_length = 64
        chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        n = max_length - len(subid)
        if n > 0:
            for _ in range(n):
                subid += chars[random.randint(0, len(chars) - 1)]
        return subid

    async def _wait_for_connection(self):
        """
        Waits until the connection is established.
        """
        while not self.connected:
            if self._is_shutting_down():
                raise Exception("Connection is closing")
            logger.debug("Waiting for connection...")
            await asyncio.sleep(1)

    async def _ratelimit(self, unit: str, max_sleep_time: int = 120) -> None:
        limit: Optional[RateLimit] = self.rate_limit.get(unit)
        if not limit:
            self.rate_limit[unit] = limit = RateLimit()

        if time.time() - limit.last_attempt_time > max_sleep_time:
            # reset backoff if action lasted more than max_sleep_time
            limit.backoff = 0
        else:
            # increase backoff
            limit.backoff = (
                min(limit.backoff * 2, max_sleep_time) if limit.backoff > 0 else 1
            )
        logger.debug("Sleeping for " + str(limit.backoff) + " seconds before " + unit)
        await asyncio.sleep(limit.backoff)
        limit.last_attempt_time = int(time.time())

    async def _subscribe(self):
        """
        [Re]Subscribe to receive nip 47 requests and responses from the relay
        """
        self.sub = MainSubscription()
        # Create requests subscription
        req_filter = {
            "kinds": [23194],
            "#p": [self.public_key_hex],
            # Since the last 3 hours (handles reboots)
            "since": int(time.time()) - 3 * 60 * 60,
        }
        self.sub.requests_sub_id = self._get_new_subid()
        # Create responses subscription (needed to track previosly responded requests)
        res_filter = {
            "kinds": [23195],
            "authors": [self.public_key_hex],
            "since": int(time.time()) - 3 * 60 * 60,
        }
        self.sub.responses_sub_id = self._get_new_subid()
        # Subscribe
        await self._send(["REQ", self.sub.requests_sub_id, req_filter])
        await self._send(["REQ", self.sub.responses_sub_id, res_filter])

    async def _on_connection(self, ws):
        """
        On connection callback, announce the service provider
        methods and subscribe to nip67 events.
        """
        # Send info event
        event = {
            "kind": 13194,
            "content": " ".join(self.supported_methods),
            "created_at": int(time.time()),
            "tags": [["p", self.public_key_hex]],
        }
        self._sign_event(event)
        await self._send(["EVENT", event])
        # Resubscribe to nwc events
        await self._subscribe()

    async def _handle_request(self, event: Dict) -> List[Dict]:
        """
        Handle a nwc request
        """
        nwc_pubkey = event["pubkey"]
        content = event["content"]
        # Decrypt the content
        content = self._decrypt_content(content, nwc_pubkey)
        # Deserialize content
        content = json.loads(content)
        # Handle request
        method = content["method"]
        listener = self.request_listeners.get(method, None)
        outs: List[Dict[str, Any]] = []
        if not listener:
            outs.append(
                {
                    "error": {
                        "code": "NOT_IMPLEMENTED",
                        "message": "Method "
                        + method
                        + " is not implemented by this service provider",
                    }
                }
            )
        else:
            try:
                results = await listener(self, nwc_pubkey, content)
                for result in results:
                    r = result[0]
                    e = result[1]
                    t = result[2] if len(result) > 2 else None
                    out = {"result": r, "error": e, "tags": t}
                    outs.append(out)
            except Exception as e:
                outs.append({"error": {"code": "INTERNAL", "message": str(e)}})
        sent_events = []
        for out in outs:
            # Finalize output
            content = {}
            content["result_type"] = method
            if "result" in out:
                content["result"] = out["result"]
            if "error" in out:
                content["error"] = out["error"]
            # Prepare response event
            res: Dict = {
                "kind": 23195,
                "created_at": int(time.time()),
                "tags": out.get("tags", []),
                "content": self._json_dumps(content),
            }
            # Reference request
            res["tags"].append(["e", event["id"]])
            # Reference user
            res["tags"].append(["p", nwc_pubkey])
            # Finalize response event
            print(res)
            res["content"] = self._encrypt_content(res["content"], nwc_pubkey)
            self._sign_event(res)

            # Register response for this request, so we knows it is not stale
            if self.sub:
                self.sub.register_response(event["id"])
            # Send response event
            await self._send(["EVENT", res])
            # Track sent events
            sent_events.append(res)
        return sent_events

    async def _on_event_message(self, msg):
        if not self.sub:
            return
        sub_id = msg[1]
        event = msg[2]
        # Ensure the event is valid (do not trust relays)
        if not self._verify_event(event):
            raise Exception("Invalid event signature")
        tags = event["tags"]
        expiration = int(next((tag for tag in tags if tag[0] == "expiration"), -1))
        # Handle event expiration if the relay doesn't support nip 40
        if expiration > 0 and expiration < int(time.time()):
            logger.debug("Event expired")
            return
        if event["kind"] == 23194 and sub_id == self.sub.requests_sub_id:
            # Ensure the request is for this service provider
            valid_p = any(
                tag[0] == "p" and tag[1] == self.public_key_hex for tag in tags
            )
            if not valid_p:
                raise Exception("Unexpected request from another service")
            # Track request
            self.sub.events[event["id"]] = event
            # if eose was received for both subscriptions, we handle the request
            # in realtime if not, we do nothing since the request may be
            # already handled or stale, all stale requests will be handled
            # later when eose is received
            if self.sub.requests_eose and self.sub.responses_eose:
                await self._handle_request(event)
        elif event["kind"] == 23195 and sub_id == self.sub.responses_sub_id:
            # Ensure the response is from this service provider
            if event["pubkey"] != self.public_key_hex:
                raise Exception("Unexpected response from another service")
            # Register as response for each e tag (request event id)
            # Note: usually we expect only one "e" tag, but we are handling
            # multiple "e" tags just in case
            etag = next((tag[1] for tag in tags if tag[0] == "e"), None)
            if etag:
                self.sub.register_response(etag)

    async def _on_eose_message(self, msg):
        if not self.sub:
            return
        sub_id = msg[1]
        # Track EOSE
        if sub_id == self.sub.requests_sub_id:
            self.sub.requests_eose = True
        elif sub_id == self.sub.responses_sub_id:
            self.sub.responses_eose = True
        # When both EOSE are receives, handle all the stale requests
        #   Note: All the requests that were received prior to the
        #         service connection and do not have a response yet,
        #         are considered stale, we will process them now
        if self.sub.requests_eose and self.sub.responses_eose:
            stales = self.sub.get_stale()
            for stale in stales:
                await self._handle_request(stale)

    async def _on_closed_message(self, msg):
        if not self.sub:
            return
        # Subscription was closed remotely.
        sub_id = msg[1]
        info = msg[2] or "" if len(msg) > 2 else ""
        # Resubscribe if one of the main subscriptions was closed
        if sub_id == self.sub.requests_sub_id or sub_id == self.sub.responses_sub_id:
            logger.warning(
                "Subscription "
                + sub_id
                + " was closed remotely: "
                + info
                + " ... resubscribing..."
            )
            await self._ratelimit("subscribing")
            await self._subscribe()

    async def _on_message(self, ws, message: str):
        """
        Handle incoming messages from the relay.
        """
        try:
            msg = json.loads(message)
            if msg[0] == "EVENT":  # Event message
                await self._on_event_message(msg)
            elif msg[0] == "EOSE":
                await self._on_eose_message(msg)
            elif msg[0] == "CLOSED":
                await self._on_closed_message(msg)
            elif msg[0] == "NOTICE":
                # A message from the relay, mostly useless, but we log it anyway
                logger.info("Notice from relay " + self.relay + ": " + str(msg[1]))
            elif msg[0] == "OK":
                pass
            else:
                raise Exception("Unknown message type " + str(msg[0]))
        except Exception as e:
            logger.error("Error parsing event: " + str(e))

    async def _connect_to_relay(self):
        """
        Initiate websocket connection to the relay.
        """
        await asyncio.sleep(1)
        logger.debug("Connecting to NWC relay " + self.relay)
        while (
            not self._is_shutting_down()
        ):  # Reconnect until the instance is shutting down
            logger.debug("Creating new connection...")
            try:
                async with websockets.connect(self.relay) as ws:
                    self.ws = ws
                    self.connected = True
                    await self._on_connection(ws)
                    while (
                        not self._is_shutting_down()
                    ):  # receive messages until the instance is shutting down
                        try:
                            reply = await ws.recv()
                            if isinstance(reply, bytes):
                                reply = reply.decode("utf-8")
                            await self._on_message(ws, reply)
                        except Exception as e:
                            logger.debug("Error receiving message: " + str(e))
                            break
                logger.debug("Connection to NWC relay closed")
            except Exception as e:
                logger.error("Error connecting to NWC relay: " + str(e))
                await asyncio.sleep(5)
            # the connection was closed, so we set the connected flag to False
            # this will make the methods calling _wait_for_connection() to wait
            # until the connection is re-established
            self.connected = False
            if not self._is_shutting_down():
                # Wait some time before reconnecting
                logger.debug("Reconnecting to NWC relay...")
                await self._ratelimit("connecting")

    def _encrypt_content(
        self, content: str, pubkey_hex: str, iv_seed: Optional[int] = None
    ) -> str:
        """
        Encrypts the content for the given public key

        Args:
            content (str): The content to be encrypted.
            pubkey_hex (str): The public key in hex format.

        Returns:
            str: The encrypted content.
        """
        pubkey = secp256k1.PublicKey(bytes.fromhex("02" + pubkey_hex), True)
        shared = pubkey.tweak_mul(bytes.fromhex(self.private_key_hex)).serialize()[1:]
        # random iv (16B)
        if not iv_seed:
            iv = Random.new().read(AES.block_size)
        else:
            iv = hashlib.sha256(iv_seed.to_bytes(32, byteorder="big")).digest()
            iv = iv[: AES.block_size]

        aes = AES.new(shared, AES.MODE_CBC, iv)

        content_bytes = content.encode("utf-8")

        # padding
        content_bytes = pad(content_bytes, AES.block_size)

        encrypted_b64 = base64.b64encode(aes.encrypt(content_bytes)).decode("ascii")
        iv_b64 = base64.b64encode(iv).decode("ascii")
        encrypted_content = encrypted_b64 + "?iv=" + iv_b64
        return encrypted_content

    def _decrypt_content(self, content: str, pubkey_hex: str) -> str:
        """
        Decrypts the content for the given public key

        Args:
            content (str): The encrypted content.
            pubkey_hex (str): The public key in hex format.

        Returns:
            str: The decrypted content.
        """
        pubkey = secp256k1.PublicKey(bytes.fromhex("02" + pubkey_hex), True)

        shared = pubkey.tweak_mul(bytes.fromhex(self.private_key_hex)).serialize()[1:]
        # extract iv and content
        (encrypted_content_b64, iv_b64) = content.split("?iv=")
        encrypted_content = base64.b64decode(encrypted_content_b64.encode("ascii"))
        iv = base64.b64decode(iv_b64.encode("ascii"))
        # Decrypt
        aes = AES.new(shared, AES.MODE_CBC, iv)
        decrypted_bytes = aes.decrypt(encrypted_content)
        decrypted_bytes = unpad(decrypted_bytes, AES.block_size)
        decrypted = decrypted_bytes.decode("utf-8")
        return decrypted

    def _verify_event(self, event: Dict) -> bool:
        """
        Verify the event signature

        Args:
            event (Dict): The event to verify.

        Returns:
            bool: True if the event signature is valid, False otherwise.
        """
        signature_data = self._json_dumps(
            [
                0,
                event["pubkey"],
                event["created_at"],
                event["kind"],
                event["tags"],
                event["content"],
            ]
        )
        event_id = hashlib.sha256(signature_data.encode()).hexdigest()
        if event_id != event["id"]:  # Invalid event id
            return False
        pubkey_hex = event["pubkey"]
        pubkey = secp256k1.PublicKey(bytes.fromhex("02" + pubkey_hex), True)
        if not pubkey.schnorr_verify(
            bytes.fromhex(event_id), bytes.fromhex(event["sig"]), None, raw=True
        ):
            return False
        return True

    def _sign_event(self, event: Dict) -> Dict:
        """
        Signs the event (in place)

        Args:
            event (Dict): The event to be signed.

        Returns:
            Dict: The input event with the signature added.
        """
        signature_data = self._json_dumps(
            [
                0,
                self.public_key_hex,
                event["created_at"],
                event["kind"],
                event["tags"],
                event["content"],
            ]
        )

        event_id = hashlib.sha256(signature_data.encode()).hexdigest()
        event["id"] = event_id
        event["pubkey"] = self.public_key_hex

        signature = (
            self.private_key.schnorr_sign(bytes.fromhex(event_id), None, raw=True)
        ).hex()
        event["sig"] = signature
        return event

    async def cleanup(self):
        logger.debug("Closing NWC Service Provider connection")
        self.shutdown = True  # Mark for shutdown
        # close tasks
        try:
            if self.reconnect_task:
                self.reconnect_task.cancel()
        except Exception as e:
            logger.warning("Error closing reconnection task: " + str(e))
        # close the websocket
        try:
            if self.ws:
                await self.ws.close()
        except Exception as e:
            logger.warning("Error closing websocket connection: " + str(e))

    class Config:
        arbitrary_types_allowed = True

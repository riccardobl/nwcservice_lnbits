import json
import os
import sys

from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
####

import random
import string

import pytest

from nwcp import NWCServiceProvider


@pytest.fixture
def nwc_service_provider():
    return NWCServiceProvider(
        "d7b5232fba0e02e32cfe26f20cdf2c803b27ecd81052c2dd5d17e5e1a333fe58", ""
    )


@pytest.fixture
def nwc_service_provider2():
    return NWCServiceProvider(
        "ce40821040275f72f3074a89770db3e2744b189f204807c867840eb58565de51", ""
    )


def test_supported_methods(nwc_service_provider):
    def make_invoice(provider, pubkey, content):
        return "invoice"

    nwc_service_provider.add_request_listener("make_invoice", make_invoice)
    s = nwc_service_provider.get_supported_methods()
    assert s == ["make_invoice"]


def test_encrytdecrypt(nwc_service_provider, nwc_service_provider2):
    content = "Hello World"
    expected_enc = "qVurNVISSl/9CfREIhk5Lg==?iv=QpCo5dI9gUcoLsSMLA7o7Q=="
    enc_a = nwc_service_provider._encrypt_content(
        content, nwc_service_provider2.public_key_hex, 21
    )
    enc_b = nwc_service_provider2._encrypt_content(
        content, nwc_service_provider.public_key_hex, 21
    )

    dec_a = nwc_service_provider2._decrypt_content(
        enc_a, nwc_service_provider.public_key_hex
    )
    dec_b = nwc_service_provider._decrypt_content(
        enc_b, nwc_service_provider2.public_key_hex
    )

    assert dec_a == content
    assert dec_b == content
    assert enc_a == expected_enc
    assert enc_b == expected_enc


def test_signverify(nwc_service_provider, nwc_service_provider2):
    # Random content
    content = ""
    for _ in range(100):
        content += random.choice(string.ascii_letters)

    tags = []
    for _ in range(random.choice([1, 2, 3, 4])):
        tags.append(
            [
                random.choice(string.ascii_letters)
                + "_"
                + random.choice(string.ascii_letters),
                random.choice(string.ascii_letters),
            ]
        )

    event = {"kind": 1, "content": content, "tags": tags, "created_at": 1234567890}

    signed = nwc_service_provider._sign_event(event)
    assert nwc_service_provider2._verify_event(signed)


@pytest.mark.asyncio
async def test_handle(nwc_service_provider, nwc_service_provider2):
    content = nwc_service_provider._json_dumps(
        {"method": "pay_invoice", "params": {"invoice": "abc"}}
    )
    content = nwc_service_provider._encrypt_content(
        content, nwc_service_provider2.public_key_hex, 21
    )
    event = {
        "kind": 23194,
        "content": content,
        "tags": [["p", nwc_service_provider2.public_key_hex]],
        "created_at": 1234567890,
    }
    signed = nwc_service_provider._sign_event(event)

    async def _handle_pay_invoice(provider, pubkey, content):
        assert pubkey == nwc_service_provider.public_key_hex
        assert content["method"] == "pay_invoice"
        assert content["params"]["invoice"] == "abc"
        return [({"preimage": "00000"}, None, [["r1", "v1"]])]

    async def _send_pass(obj):
        pass

    nwc_service_provider2._send = _send_pass
    nwc_service_provider2.add_request_listener("pay_invoice", _handle_pay_invoice)
    sent_events = await nwc_service_provider2._handle_request(signed)
    assert len(sent_events) == 1
    for revent in sent_events:
        assert nwc_service_provider2._verify_event(revent)
        content = nwc_service_provider2._decrypt_content(
            revent["content"], nwc_service_provider.public_key_hex
        )
        logger.debug(event)
        logger.debug(revent)
        content = json.loads(content)
        assert content["result_type"] == "pay_invoice"
        assert content["result"]["preimage"] == "00000"
        tags = revent["tags"]
        r1_tag = [tag for tag in tags if tag[0] == "r1"]
        assert len(r1_tag) == 1
        assert r1_tag[0][1] == "v1"

        e_tag = [tag for tag in tags if tag[0] == "e"]
        assert len(e_tag) == 1
        assert e_tag[0][1] == event["id"]

        p_tag = [tag for tag in tags if tag[0] == "p"]
        assert len(p_tag) == 1
        assert p_tag[0][1] == nwc_service_provider.public_key_hex

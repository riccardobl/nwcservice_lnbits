  window.app = Vue.createApp({
    el: "#vue",
    mixins: [windowMixin],
    delimiters: ["${", "}"],
    data: function () {
      return {
        selectedWallet: null,
        nodePermissions: [],
        nwcEntries: [],
        nwcsTable: {
          columns: [
            {
              name: "description",
              align: "left",
              label: "Description",
              field: "description",
            },
            { name: "status", align: "left", label: "Status", field: "status" },
            {
              name: "last_used",
              align: "left",
              label: "Last used",
              field: "last_used",
            },
            {
              name: "created_at",
              align: "left",
              label: "Created",
              field: "created_at",
            },
            {
              name: "expires_at",
              align: "left",
              label: "Expires",
              field: "expires_at",
            },
          ],
          pagination: {
            rowsPerPage: 10,
          },
        },
        connectDialog: {
          show: false,
          data: {},
        },
        pairingDialog: {
          show: false,
          data: {
            pairingUrl: "",
          },
        },
        pairingQrDialog: {
          show: false,
          data: {
            pairingUrl: "",
          },
        },
        connectionInfoDialog: {
          show: false,
          data: {},
        },
      };
    },

    methods: {
      showConnectDialog() {
        const wallet = this.getWallet();
        if (!wallet) {
          Quasar.Notify.create({
            type: 'negative',
            message: 'Please select a wallet first',
          });
          return;
        } else {
          this.connectDialog.show = true;
        }
      },
      openConnectionInfoDialog(data) {
        this.connectionInfoDialog.data = data;
        this.connectionInfoDialog.show = true;
      },
      closeConnectionInfoDialog() {
        this.connectionInfoDialog.show = false;
      },
      openPairingUrl() {
        const url = this.pairingDialog.data.pairingUrl;
        if (url) window.open(url, "_blank");
      },
      go(url) {
        window.open(url, "_blank");
      },
      async copyPairingUrl() {
        const url = this.pairingDialog.data.pairingUrl;
        if (url) {
          try {
            await navigator.clipboard.writeText(url);
            Quasar.Notify.create({
              type: "positive",
              message: "URL copied to clipboard"
            });
          } catch (err) {
            Quasar.Notify.create({
              type: "negative",
              message: "Failed to copy URL."
            });
          }
        }
      },
      showPairingQR() {
        this.pairingQrDialog.data.pairingUrl =
          this.pairingDialog.data.pairingUrl;
        this.pairingQrDialog.show = true;
      },
      closePairingQrDialog() {
        this.pairingQrDialog.show = false;
      },
      loadConnectDialogData() {
        this.connectDialog.data = {
          description: "",
          expires_at: Date.now() + 1000 * 60 * 60 * 24 * 7,
          neverExpires: true,
          permissions: [],
          budgets: [],
        };
        for (const permission of this.nodePermissions) {
          this.connectDialog.data.permissions.push({
            key: permission.key,
            name: permission.name,
            value: permission.value,
          });
        }
      },
      deleteBudget(index) {
        this.connectDialog.data.budgets.splice(index, 1);
      },
      addBudget() {
        this.connectDialog.data.budgets.push({
          budget_sats: 1000,
          used_budget_sats: 0,
          created_at:
            new Date(new Date().setHours(0, 0, 0, 0)).getTime() / 1000,
          expiration: "never",
        });
      },
      closeConnectDialog() {
        this.connectDialog.show = false;
        this.loadConnectDialogData();
      },
      getWallet: function () {
        let wallet = undefined;
        for (let i = 0; i < this.g.user.wallets.length; i++) {
          if (this.g.user.wallets[i].id == this.selectedWallet) {
            wallet = this.g.user.wallets[i];
            break;
          }
        }
        return wallet;
      },
      async generateKeyPair() {
        while (!window.NobleSecp256k1) {
          await new Promise((resolve) => setTimeout(resolve, 1));
        }
        const privKeyBytes = window.NobleSecp256k1.utils.randomPrivateKey();
        const pubKeyBytes = window.NobleSecp256k1.getPublicKey(privKeyBytes);
        const out = {
          privKeyBytes: privKeyBytes,
          pubKeyBytes: pubKeyBytes,
          privKey: window.NobleSecp256k1.etc.bytesToHex(privKeyBytes),
          pubKey: window.NobleSecp256k1.etc.bytesToHex(pubKeyBytes.slice(1)),
        };
        return out;
      },
      deleteNWC: async function (pubkey) {
        Quasar
          .Dialog.create({
            title: "Confirm Deletion",
            message: "Are you sure you want to delete this connection?",
            cancel: true,
            persistent: true,
          })
          .onOk(async () => {
            try {
              const wallet = this.getWallet();
              const response = await LNbits.api.request(
                "DELETE",
                `/nwcprovider/api/v1/nwc/${pubkey}`,
                wallet.adminkey,
              );
              this.loadNwcs();
              Quasar.Notify.create({
                type: "positive",
                message: "Deleted successfully"
              });
            } catch (error) {
              LNbits.utils.notifyApiError(error);
            }
          })
          .onCancel(() => {
            // User canceled the operation
          });
      },
      loadNwcs: async function () {
        const wallet = this.getWallet();
        if (!wallet) {
          this.nwcs = [];
          return;
        }
        try {
          const response = await LNbits.api.request(
            "GET",
            "/nwcprovider/api/v1/nwc?include_expired=true&calculate_spent_budget=true",
            wallet.adminkey,
          );
          this.nwcs = response.data;
        } catch (error) {
          this.nwcs = [];
        }
        try {
          const response = await LNbits.api.request(
            "GET",
            "/nwcprovider/api/v1/permissions",
            wallet.adminkey,
          );
          const permissions = [];
          for (const [key, value] of Object.entries(response.data)) {
            permissions.push({
              key: key,
              name: value.name,
              value: value.default,
            });
          }
          this.nodePermissions = permissions;
        } catch (error) {
          Lnbits.utils.notifyApiError(error);
        }
        this.loadConnectDialogData();
        const newTableEntries = [];
        for (const nwc of this.nwcs) {
          const t = Quasar.date.formatDate(
            new Date(nwc.data.created_at * 1000),
            "YYYY-MM-DD HH:mm",
          );
          const e =
            nwc.data.expires_at > 0
              ? Quasar.date.formatDate(
                  new Date(nwc.data.expires_at * 1000),
                  "YYYY-MM-DD HH:mm",
                )
              : "Never";
          const l = Quasar.date.formatDate(
            new Date(nwc.data.last_used * 1000),
            "YYYY-MM-DD HH:mm",
          );
          const nwcTableEntry = {
            description: nwc.data.description,
            created_at: t,
            expires_at: e,
            last_used: l,
            pubkey: nwc.data.pubkey,
            permissions: nwc.data.permissions,
            budgets: [],
            status: "Active",
          };
          if (
            nwc.data.expires_at > 0 &&
            nwc.data.expires_at < new Date().getTime() / 1000
          ) {
            nwcTableEntry.status = "Expired";
          }
          for (const budget of nwc.budgets) {
            const createdAt = Quasar.date.formatDate(
              new Date(budget.created_at * 1000),
              "YYYY-MM-DD HH:mm",
            );
            let refreshWindow = budget.refresh_window;
            if (refreshWindow <= 0) {
              refreshWindow = "Never";
            } else if (refreshWindow == 60 * 60 * 24) {
              refreshWindow = "Daily";
            } else if (refreshWindow == 60 * 60 * 24 * 7) {
              refreshWindow = "Weekly";
            } else if (refreshWindow == 60 * 60 * 24 * 30) {
              refreshWindow = "Monthly";
            } else if (refreshWindow == 60 * 60 * 24 * 365) {
              refreshWindow = "Yearly";
            }
            nwcTableEntry.budgets.push({
              budget_sats: budget.budget_msats / 1000,
              used_budget_sats: budget.used_budget_msats / 1000,
              created_at: createdAt,
              refresh_window: refreshWindow,
            });
          }
          newTableEntries.push(nwcTableEntry);
        }
        this.nwcEntries = newTableEntries;
      },
      closePairingDialog() {
        this.pairingDialog.show = false;
      },
      async showPairingDialog(secret) {
        let response = await LNbits.api.request(
          "GET",
          "/nwcprovider/api/v1/pairing/{SECRET}",
        );
        response = response.data;
        response = response.replace("{SECRET}", secret);
        this.pairingDialog.data.pairingUrl = response;
        this.pairingDialog.show = true;
      },
      async confirmConnectDialog() {
        const keyPair = await this.generateKeyPair();
        // timestamp
        let expires_at = 0;
        if (!this.connectDialog.data.neverExpires) {
          expires_at =
            new Date(this.connectDialog.data.expires_at).getTime() / 1000;
        }
        const data = {
          permissions: [],
          description: this.connectDialog.data.description,
          expires_at: expires_at,
          budgets: [],
        };
        for (const permission of this.connectDialog.data.permissions) {
          if (permission.value) data.permissions.push(permission.key);
        }
        for (const budget of this.connectDialog.data.budgets) {
          const budget_msats = budget.budget_sats * 1000;
          let refresh_window = 0;
          switch (budget.expiry) {
            case "Daily":
              refresh_window = 60 * 60 * 24;
              break;
            case "Weekly":
              refresh_window = 60 * 60 * 24 * 7;
              break;
            case "Monthly":
              refresh_window = 60 * 60 * 24 * 30;
              break;
            case "Yearly":
              refresh_window = 60 * 60 * 24 * 365;
              break;
            case "Never":
              refresh_window = 0;
              break;
          }
          data.budgets.push({
            budget_msats: budget_msats,
            refresh_window: refresh_window,
            created_at:
              new Date(new Date().setHours(0, 0, 0, 0)).getTime() / 1000,
          });
        }
        const wallet = this.getWallet();

        try {
          const response = await LNbits.api.request(
            "PUT",
            "/nwcprovider/api/v1/nwc/" + keyPair.pubKey,
            wallet.adminkey,
            data,
          );
          this.closeConnectDialog();
          if (
            !response.data ||
            !response.data.data ||
            !response.data.data.pubkey
          ) {
            LNbits.utils.notifyApiError("Error creating nwc pairing");
            return;
          }
          this.showPairingDialog(keyPair.privKey);
        } catch (error) {
          LNbits.utils.notifyApiError(error);
        }
        this.loadNwcs();
      },
    },

    created: function () {
      this.loadNwcs();
    },
    watch: {
      selectedWallet(newValue, oldValue) {
        this.loadNwcs();
      },
    },
  });
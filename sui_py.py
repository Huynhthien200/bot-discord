# sui_py.py – auto-detect layout
from importlib import import_module, util

# ----- tìm module client -----
if util.find_spec("sui.client"):                     # bản 0.1.7+ …
    _client = import_module("sui.client")
    _builder = import_module("sui.transaction_builder")
else:                                               # bản 0.1.1 …
    _client = import_module("sui")                  # lớp nằm ngay gốc
    _builder = import_module("sui.txn_builder")     # builder gốc

# ----- re-export -----
SuiClient  = getattr(_client, "SuiClient", _client.SyncClient)
SuiAccount = _client.SuiAccount
SyncClient = getattr(_client, "SyncClient", _client.SuiClient)
sui_txn    = _builder

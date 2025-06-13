"""
sui_py.py  – shim tối giản, KHÔNG chạm thư viện ML
"""
from importlib import import_module as _imp

_client  = _imp("sui.client")
_builder = _imp("sui.transaction_builder")

SuiClient  = _client.SuiClient
SuiAccount = _client.SuiAccount
SyncClient = _client.SuiClient      # alias
sui_txn    = _builder               # re-export builder module

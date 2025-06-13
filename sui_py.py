"""
sui_py.py – shim tối giản
Chỉ import phần core của package 'sui', tránh các thư viện ML.
"""
from importlib import import_module as _imp

_client  = _imp("sui.client")
_builder = _imp("sui.transaction_builder")

SuiClient  = _client.SuiClient
SuiAccount = _client.SuiAccount
SyncClient = _client.SuiClient      # alias cho code cũ
sui_txn    = _builder               # module builder

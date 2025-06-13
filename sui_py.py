"""
sui_py.py  –  shim cho code cũ
Chỉ import phần core của package 'sui' (client & txn builder),
tránh thư viện ML nặng, nên không cần numpy/sklearn.
"""
from importlib import import_module as _imp

_client  = _imp("sui.client")
_builder = _imp("sui.transaction_builder")

SuiClient  = _client.SuiClient
SuiAccount = _client.SuiAccount
SyncClient = _client.SuiClient        # alias cho code cũ
sui_txn    = _builder                 # re-export module builder

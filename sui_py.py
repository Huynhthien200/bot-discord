# sui_py.py  – shim tối giản, tránh thư viện ML
from importlib import import_module

core = import_module("sui.client")
txn  = import_module("sui.transaction_builder")

SuiClient  = core.SuiClient
SuiAccount = core.SuiAccount
SyncClient = core.SuiClient      # alias để code cũ dùng
sui_txn    = txn                 # module builder

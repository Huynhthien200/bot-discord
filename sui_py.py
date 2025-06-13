# sui_py.py  – chỉ export phần core, tránh import thư mục 'ml'
from importlib import import_module

client_mod = import_module("sui.client")           # core client
txn_mod    = import_module("sui.transaction_builder")

SuiClient  = client_mod.SuiClient
SuiAccount = client_mod.SuiAccount
SyncClient = client_mod.SuiClient   # alias, tương đương
sui_txn    = txn_mod                # transaction builder module

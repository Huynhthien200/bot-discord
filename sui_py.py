"""
sui_py.py  –  shim tối giản
• Chỉ import phần core của package 'sui'
• Không động tới 'sui.ml' nên KHÔNG cần numpy/pandas/sklearn
"""

from importlib import import_module as _imp

# --- import các module core cần dùng ---
_client  = _imp("sui.client")                 # lớp SuiClient, SuiAccount
_builder = _imp("sui.transaction_builder")    # builder giao dịch

# --- Re-export cho code cũ sử dụng ---
SuiClient  = _client.SuiClient
SuiAccount = _client.SuiAccount
SyncClient = _client.SuiClient       # alias cho code hiện tại
sui_txn    = _builder                # module builder (chứa TransferSui ...)

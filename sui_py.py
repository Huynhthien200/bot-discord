# ─── sui_py.py  (đặt cạnh main.py) ─────────────────────────────
"""
Shim linh-hoạt cho mọi phiên bản 'sui' 0.1.x
• Tự phát hiện nơi chứa SuiClient / SuiAccount
• Tự tìm transaction builder (transaction_builder  hoặc  txn_builder)
"""

from importlib import import_module, util

# ----- lấy module client -----
if util.find_spec("sui.client"):                 # 0.1.7+
    _client = import_module("sui.client")
else:                                           # 0.1.1, 0.1.0 …
    _client = import_module("sui")              # lớp nằm thẳng gốc

# ----- lấy module builder -----
if util.find_spec("sui.transaction_builder"):   # mới
    _builder = import_module("sui.transaction_builder")
elif util.find_spec("sui.txn_builder"):         # cũ
    _builder = import_module("sui.txn_builder")
else:
    raise ImportError("Không tìm thấy transaction builder trong gói 'sui'")

# ----- re-export cho code cũ -----
SuiAccount = getattr(_client, "SuiAccount")
SuiClient  = getattr(_client, "SuiClient", None) or getattr(_client, "SyncClient")
SyncClient = getattr(_client, "SyncClient", SuiClient)
sui_txn    = _builder
# ────────────────────────────────────────────────────────────────

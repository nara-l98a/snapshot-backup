# snapshot-backup

`snapshot-backup` 是一个 Python 3.10+ 本地目录版本化快照 CLI：为目录生成唯一快照，复制普通文件并写入 SHA-256 清单，支持列出与完整性验证。

## 功能边界与安全

仅使用标准库，不压缩、加密、上传或删除数据。递归时不跟随符号链接，链接和非普通文件记录为跳过；输出目录解析为绝对路径后不得位于源目录内。源文件和旧快照永不删除。每个快照的文件树保存在 `data/`，校验清单 `manifest.json` 位于快照根目录，避免源目录里恰好有同名清单文件时覆盖原内容。状态 JSON 和清单使用同目录临时文件与 `os.replace` 写入。不会复制权限、所有者、时间戳、设备文件或 FIFO。快照可能包含敏感内容，请保护输出目录；状态 JSON 与清单会包含用户路径。程序不读取或发送网络数据。

## Python、安装与完整 CLI

需要 Python **3.10+**，无第三方运行依赖：

```bash
python -m pip install .
# 开发环境可用：python -m pip install -e .
snapshot-backup --help
```

```text
snapshot-backup [--data PATH | --db PATH] create SOURCE --output OUTPUT
snapshot-backup [--data PATH | --db PATH] list [--json]
snapshot-backup [--data PATH | --db PATH] verify SNAPSHOT_ID_OR_DIRECTORY
```

`--data/--db` 为状态 JSON 路径；省略时依次使用 `SNAPSHOT_BACKUP_DATA`、`~/.snapshot-backup.json`。`create` 的 `SOURCE` 必须为目录，`--output/-o` 为源目录之外的输出根目录；`list --json` 输出 JSON；`verify` 接受快照 ID 或含 `manifest.json` 的目录。ID 是 UTC 时间加随机后缀，不覆盖旧快照。

## 完整示例

```bash
mkdir -p /tmp/sb-source
cp examples/example-data.txt /tmp/sb-source/
SNAPSHOT_BACKUP_DATA=/tmp/sb-state.json snapshot-backup create /tmp/sb-source -o /tmp/sb-backups
SNAPSHOT_BACKUP_DATA=/tmp/sb-state.json snapshot-backup list
SNAPSHOT_BACKUP_DATA=/tmp/sb-state.json snapshot-backup verify <快照 ID>
```

创建后的结构：

```text
<输出根目录>/<快照 ID>/
├── data/                 # 源目录中的普通文件树
└── manifest.json         # SHA-256、字节数和跳过项
```

## 数据格式、隐私与开发

状态 JSON 含 `version`、`snapshots`（id、path、source、created_at、file_count）；快照 `manifest.json` 含格式版本、源路径、时间、相对文件路径、字节数、SHA-256 和跳过项。`examples/` 无个人数据、凭据、Token 或密码。测试使用 `tempfile`，覆盖创建、完整性校验、篡改、输出边界、符号链接、路径穿越防护、用户同名文件和旧快照保留：

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

## 许可证

MIT，详见 [LICENSE](LICENSE)。

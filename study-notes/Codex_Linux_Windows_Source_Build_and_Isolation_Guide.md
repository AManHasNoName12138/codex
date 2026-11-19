# Codex Linux / Windows 源码构建、独立安装与数据隔离手册

更新：2026-11-19。参考 [OpenCode 源码构建与隔离手册](https://github.com/AManHasNoName12138/opencode/blob/study-notes/study-notes%2FOpenCode_Linux_Windows_Source_Build_and_Isolation_Guide.md)的组织方式，按 **Codex 当前实现**重新核对构建产物、配置、认证、数据库和系统服务。适用于 CLI/TUI；不构建桌面安装包。

源码基线：[c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee](https://github.com/AManHasNoName12138/codex/tree/c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee)。该基线固定 Rust **1.95.0**，Cargo workspace 版本为 **0.0.0**。后续改用其他提交时，应重新核对本文引用的源码。

最终入口：**官方版保持原来的 `codex`；源码版使用独立的 `codex-dev` 启动器。** 源码、完整程序包、应用配置、登录文件、数据库、日志、缓存和测试项目分别放入开发目录，不覆盖官方安装，也不修改当前终端的 `CODEX_HOME`。

## 1. 先确定隔离级别

| 需求                                                     | 应采用的方案                                                       | 能保证的边界                                                                          |
| -------------------------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| 同一机器上学习 CLI/TUI，分别保存配置、凭据和会话         | 本文专用目录、启动器、独立项目副本，加第 6 节验收                  | 在约定使用方式下分开应用状态与安装产物；仍使用同一操作系统权限。                      |
| 要求修改后的源码、插件或系统服务也不能影响官方版         | **独立虚拟机，作为严格隔离的默认方案**；在虚拟机内执行本文构建流程 | 官方程序与数据留在宿主机，开发机不挂载官方目录、不共享凭据与服务。                    |
| 测试 Windows 提权沙箱、沙箱安装/卸载、后台服务或全局更新 | **独立 Windows 虚拟机**                                            | 分开系统账户、服务、注册表、ACL 和应用数据；只换 Windows 用户仍不足以隔离机器级对象。 |

这里的“互不影响”需要包含使用条件：两套程序不访问同一项目副本、不连接同一 app-server 或 MCP 后端、不将目录软链接/硬链接到一起。若需要云端数据也隔离，应使用单独的测试账号、组织/工作区和 API key；本地目录不能分开同一账号的云端数据、配额和服务端操作。

### 1.1 严格隔离的操作顺序

1. 新建 Linux 或 Windows 虚拟机，使用独立虚拟磁盘和开发账号。Linux 示例采用 Ubuntu 24.04；Windows 示例采用 Windows 11 x64。
2. 将官方安装和官方数据保留在宿主机。不要把宿主机的用户目录、`.codex`、工作项目、Docker socket、SSH agent 或凭据目录挂载/转发进虚拟机。
3. 在虚拟机里重新 clone 源码，安装构建工具并单独登录测试账号。只传递源码提交或不含凭据的补丁。
4. 在虚拟机内同样使用下文开发目录与启动器。宿主机与虚拟机分别验收；无需为了验收而把官方凭据复制进虚拟机。

WSL2 可以按 Linux 流程构建，但默认可访问 `/mnt/c` 且可能启用 Windows 程序互操作，不能直接视为与宿主 Windows 完全隔离的虚拟机。严格方案使用不共享宿主目录的独立 VM；若仅做应用数据并存，WSL2 的源码、`CODEX_HOME` 和 SQLite 都放在发行版的 Linux 文件系统中，不与原生 Windows 版共用。

## 2. Codex 中影响构建和隔离的实现

### 2.1 使用完整程序包

当前仓库的 [build_codex_package.py](../scripts/build_codex_package.py) 会通过 Cargo 一起构建所需程序，再按[标准布局](../scripts/codex_package/README.md)组装：

```text
releases/<构建编号>/
├── codex-package.json
├── bin/
│   ├── codex[.exe]
│   ├── codex-code-mode-host[.exe]
│   └── codex-exec-server[.exe]
├── codex-resources/
│   ├── bwrap                           # Linux
│   ├── codex-command-runner.exe         # Windows
│   └── codex-windows-sandbox-setup.exe  # Windows
└── codex-path/
    └── rg[.exe]
```

源码开发版必须保留整个包和内部文件名；只给外层启动器命名为 `codex-dev`。不要单独复制或改名 `bin/codex` 后把其余文件丢掉，也不要混用官方版的辅助程序。包中的资源路径、执行服务和代码运行宿主需要相互匹配。

构建器要求 `CODEX_REPO_ROOT` 指向仓库根目录；`just assemble-codex-package` 会自动设置它，本文启动器的构建模式也会设置。Linux 默认打包目标是 **musl**，本文显式选择 **GNU/glibc** 本机目标，避免意外进入交叉编译流程。默认 `dev-small` profile 优先减小本地构建体积；需要优化版本时改用 `release`。

构建器还会下载并校验对应目标的 V8 归档/绑定文件和 ripgrep。当前 [v8.py](../scripts/codex_package/v8.py) 对 Windows 也有资产选择逻辑；不要照旧说明手工混入不匹配的 V8 文件。`--package-version` 写入包清单，不修改 `Cargo.toml`；本文使用 `0.0.0-local.<时间>.g<提交>` 标识独立构建。构建器内部调用 `cargo build`，**没有自动添加 `--locked`**：先执行 `cargo fetch --locked`，构建后再检查 lockfile 是否变化。

### 2.2 数据路径不能只看 `CODEX_HOME`

| 对象                                   | 官方版常见位置或机制                                                     | 本文开发版位置或处理                                                          |
| -------------------------------------- | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| 程序与辅助程序                         | 取决于 standalone、npm、包管理器或 Windows 安装方式                      | `ROOT/releases/<编号>/`，完整保留布局                                         |
| 当前构建                               | 官方安装器管理                                                           | `ROOT/current.txt`，内容为一个构建目录名                                      |
| 用户配置                               | 默认 `~/.codex/config.toml`，可被 `CODEX_HOME` 改写                      | `ROOT/codex-home/config.toml`                                                 |
| CLI 认证                               | 文件或系统凭据存储                                                       | 显式 `cli_auth_credentials_store="file"`，使用开发 `auth.json`                |
| MCP OAuth                              | 文件或系统凭据存储                                                       | 显式 `mcp_oauth_credentials_store="file"`，使用开发 `.credentials.json`       |
| 会话、历史、技能、插件、主题、安装状态 | 多数从 `CODEX_HOME` 派生                                                 | `ROOT/codex-home/` 内各自生成；不复制官方目录                                 |
| SQLite 状态                            | 配置 `sqlite_home` 优先，其次 `CODEX_SQLITE_HOME`，再回退到 `CODEX_HOME` | 环境变量和配置都固定为 `ROOT/sqlite`                                          |
| 日志                                   | 默认 `CODEX_HOME/log`；显式 `log_dir` 也影响本地 SQLite 日志位置         | `ROOT/logs`，与开发状态库一起备份                                             |
| 普通 home、XDG、Windows 应用目录       | 用户 home 和系统已知目录；有些读取与 `CODEX_HOME` 无关                   | 子进程环境使用 `ROOT/home`、`ROOT/xdg/*`；Windows OS 已知目录仍属于真实账号   |
| 临时文件、下载缓存                     | OS 临时目录、Cargo 缓存等                                                | `ROOT/tmp`、`ROOT/tools/cargo`、`ROOT/tools/rustup`、`ROOT/target`            |
| 项目配置与指令                         | 项目及祖先目录中的 `.codex`、`.agents`、`AGENTS.md` 等                   | 只使用 `ROOT/workspaces/` 下的独立副本，首次运行前检查这些文件及链接          |
| app-server/远程会话                    | 本地、远程端点或 daemon 状态                                             | 本文只运行默认进程内客户端，启动器不开放 daemon、remote-control 或 `--remote` |

依据：[home 解析](../codex-rs/utils/home-dir/src/lib.rs)、[配置解析](../codex-rs/core/src/config/mod.rs)、[配置字段](../codex-rs/config/src/config_toml.rs)、[认证文件](../codex-rs/login/src/auth/storage.rs)、[MCP OAuth](../codex-rs/rmcp-client/src/oauth.rs)。`CODEX_HOME` 指定的目录必须已经存在，源码会对其做规范化；指向官方目录的符号链接不能构成隔离。

文件认证设置只针对 CLI 和 MCP OAuth。其他秘密存储仍可能使用系统钥匙串；[SecretsManager](../codex-rs/secrets/src/lib.rs)的账户标识含规范化 `CODEX_HOME` 的哈希，和默认目录分开，但这仍不等同于独立 OS 账号。不要将官方 `auth.json`、`.credentials.json`、`secrets/` 或整个配置目录复制到开发目录。

### 2.3 两个不能靠环境变量隔离的边界

**系统/受管配置。** [配置加载器](../codex-rs/config/src/loader/README.md)还会读 Linux 的 `/etc/codex/`，以及 Windows ProgramData 下的 `OpenAI/Codex`；Windows 注册表 `HKLM\SOFTWARE\Policies\OpenAI\Codex` 的 `RequirementsPath` 也可指定要求文件。Windows 使用系统已知目录 API，改 `ProgramData` 环境变量并不会移动该位置。部分受管设置高于会话参数，不能假定 `-c sqlite_home=...` 一定覆盖它们。

本文启动器发现这些系统配置或 Windows `RequirementsPath` 时会拒绝运行 Codex，并提示改用独立 VM。它不修改、不删除、不绕过组织策略。登录后若云端受管要求或有效路径与开发目录冲突，也应停止该并存流程，在经授权的独立测试环境处理。

**Windows 提权沙箱。** 当前 [setup.rs](../codex-rs/windows-sandbox-rs/src/setup.rs)使用固定账户 `CodexSandboxOffline` 和 `CodexSandboxOnline`；[service_identity.rs](../codex-rs/windows-sandbox-rs/src/service_identity.rs)说明未打包程序使用默认系统服务名称，部分身份和账户仍是共享的。不同 `CODEX_HOME`、不同用户或不同程序目录都不能据此宣称机器级隔离。

同机启动器固定 `[windows] sandbox="unelevated"`、`allow_mxc=false`，使用当前源码支持的受限令牌路径，避免进入提权部署或自动 MXC 选择流程。这改变了开发版的运行模式，不能用来验证提权沙箱行为。要测试提权模式，在独立 Windows VM 内单独配置和验证；遇到组织要求强制另一种沙箱时，不在宿主机上放宽要求。

## 3. 配套启动器与目录约定

配套文件：

- [codex-dev.py](scripts/codex-dev.py)：Linux/Windows 共用的环境、目录、包检查和命令分派实现，要求 Python **3.12+**。
- [codex-dev.sh](scripts/codex-dev.sh)：Linux Bash 入口，安装时命名为 `codex-dev`。
- [codex-dev.ps1](scripts/codex-dev.ps1)：Windows 入口，要求 **PowerShell 7.3+**。
- [test_codex_dev.py](scripts/test_codex_dev.py)：使用临时目录与模拟程序的隔离验证。

将对应平台的入口和 `codex-dev.py` **复制到同一个全新的开发根目录**。不要从仓库的 `study-notes/scripts` 中直接初始化。开发根目录简称 `ROOT`：Linux 示例为 `~/codex-dev`，Windows 示例为 `%LOCALAPPDATA%\CodexDev`，也可选择专用磁盘上的短路径。

```text
ROOT/
├── codex-dev / codex-dev.ps1 / codex-dev.py
├── .codex-dev-root
├── current.txt
├── releases/
├── codex-home/        # config.toml、独立登录、会话及扩展状态
├── sqlite/           # 开发状态数据库
├── logs/             # 开发日志，包括显式 log_dir 的本地 SQLite 日志
├── home/、xdg/、runtime/、tmp/
├── tools/cargo/、tools/rustup/、target/、downloads/
└── workspaces/
    ├── codex/        # 独立源码 clone
    └── scratch/      # 默认测试项目
```

启动器的工作方式：

- 只为子进程构造环境，不修改父终端、用户 profile、系统环境变量或全局 PATH。不要 `source` / dot-source 入口。
- 从白名单保留系统、终端、代理、证书和 MSVC 构建变量，重新设置开发路径；不继承父进程的 `CODEX_*`、API key、Git 凭据、shell 启动钩子、V8/Cargo 自定义构建覆盖项。
- 主运行模式会检查固定配置、共享系统配置、逃出开发根目录的状态链接和硬链接，检查完整程序包，再通过绝对路径启动 `codex`。这些检查用于发现误配置，不构成抗恶意代码或并发路径替换的文件访问沙箱。
- 默认从 `workspaces/scratch` 启动；用 `--project` 显式选择 `ROOT/workspaces/` 内的另一个项目。不会沿用用户恰好所在的官方项目工作目录。
- 支持 `tui`、`exec`、`login`、`logout`、`resume`、`fork`、`doctor`、`--version`、`--help`；拦截配置、profile、目录和远程端点覆盖。入口未开放更新、桌面启动、daemon、远程控制和系统安装子命令。
- `--exec /绝对路径/工具 参数...` 是安装 Rust、Git 操作和构建的辅助模式，保留当前目录；**它不执行 Codex 运行模式的检查，不用它绕过启动器运行 Codex**。它的 PATH 保留系统工具条目，裸 `codex` 仍可能命中官方版。开发运行统一调用本启动器。
- `--paths` 只显示启动器将设置的路径，不显示密钥，也不是 Codex 最终有效配置的证明；运行后仍需第 6 节验收。

开发配置可以增加模型等普通设置，但启动器管理的路径、文件认证、更新检查和 Windows 沙箱字段必须保持初始化值。它也拒绝 `codex-home/.env`：Codex 的 dotenv 加载可改变非 `CODEX_` 变量，复制现有 `.env` 容易重新引入官方身份或目录。登录使用专用 `login` 命令。代理和证书可通过启动器白名单中的变量传入；不要为了解决网络问题复制官方整套环境。

Windows 的 `USERPROFILE`/`APPDATA` 子进程环境不会改变 OS 账号、注册表或所有 Known Folder API 的结果。[技能发现](../codex-rs/ext/skills/src/host_roots.rs)等功能还会读取 home 相关位置。使用新开发账号或独立 VM；不要把“修改了环境变量”理解为所有全局资源都已转移。

## 4. Linux：源码构建与安装

以下按 Ubuntu 24.04 / Debian 系的 **本机 glibc 构建**编写，使用 Bash。其他发行版安装等价依赖；Alpine/musl 和跨架构构建需要额外工具链，见仓库 CI，不直接替换本文目标名后照抄。

### 4.1 准备工具和独立目录

在选定的开发机器中准备依赖。Ubuntu 24.04 自带 Python 3.12；旧系统须先提供 Python 3.12+。下面的系统包安装用于开发机，不安装或更新 Codex 官方版。

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates python3 build-essential \
  pkg-config libcap-dev libasound2-dev libssl-dev cmake clang ninja-build perl nasm
python3 --version
```

`libcap-dev` 用于构建包中的 bubblewrap，`libasound2-dev` 用于 Linux 音频依赖；C/C++、CMake 等用于原生依赖。依赖构建错误应按具体包修复，不通过跳过沙箱辅助程序来凑齐安装包。

从**包含本手册和 scripts 的仓库根目录**开始，在同一 Bash 终端依次执行：

```bash
set -euo pipefail
CD_GUIDE_REPO="$(git rev-parse --show-toplevel)"
CD_ROOT="$HOME/codex-dev"
CD_LAUNCHER="$CD_ROOT/codex-dev"
CD_SOURCE="$CD_ROOT/workspaces/codex"
CD_PIN=c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee
CD_GIT="$(command -v git)"
CD_PYTHON="$(command -v python3)"
CD_OFFICIAL="$(type -P codex || true)"

type -a codex || true
test ! -e "$CD_ROOT"  # 已安装开发版的更新见第 7 节
umask 077
mkdir -p "$CD_ROOT"
install -m 755 "$CD_GUIDE_REPO/study-notes/scripts/codex-dev.sh" "$CD_LAUNCHER"
install -m 644 "$CD_GUIDE_REPO/study-notes/scripts/codex-dev.py" "$CD_ROOT/codex-dev.py"
"$CD_LAUNCHER" --init
"$CD_LAUNCHER" --paths
if [[ -n "$CD_OFFICIAL" ]]; then
  "$CD_OFFICIAL" --version
  sha256sum "$CD_OFFICIAL" > "$CD_ROOT/official-binary.before.sha256"
fi
```

如果官方入口是 alias、npm shim 或安装器链接，另行记录其实际命令和完整官方安装目录；`CD_OFFICIAL` 不一定就是最终原生二进制。严格 VM 内可以没有官方安装，宿主机单独保留官方信息。

### 4.2 安装开发目录专用 Rust 工具链

直接下载官方 rustup 引导程序，安装时显式禁止修改 PATH。启动器让 `CARGO_HOME`、`RUSTUP_HOME` 和 home 指向开发目录，不升级已有用户的 Rust 安装。

```bash
case "$(uname -m)" in
  x86_64) CD_TARGET=x86_64-unknown-linux-gnu ;;
  aarch64|arm64) CD_TARGET=aarch64-unknown-linux-gnu ;;
  *) echo '本文只覆盖 Linux x64 / ARM64 本机 glibc 构建' >&2; exit 1 ;;
esac
curl -fL "https://static.rust-lang.org/rustup/dist/$CD_TARGET/rustup-init" \
  -o "$CD_ROOT/downloads/rustup-init"
chmod 700 "$CD_ROOT/downloads/rustup-init"
"$CD_LAUNCHER" --exec "$CD_ROOT/downloads/rustup-init" \
  -y --no-modify-path --profile minimal --default-toolchain 1.95.0
CD_CARGO="$CD_ROOT/tools/cargo/bin/cargo"
CD_RUSTUP="$CD_ROOT/tools/cargo/bin/rustup"
"$CD_LAUNCHER" --exec "$CD_RUSTUP" component add rustfmt clippy rust-src --toolchain 1.95.0
"$CD_LAUNCHER" --exec "$CD_RUSTUP" target add "$CD_TARGET" --toolchain 1.95.0
"$CD_LAUNCHER" --exec "$CD_CARGO" --version
```

### 4.3 获取固定源码并构建完整包

```bash
"$CD_LAUNCHER" --exec "$CD_GIT" clone --branch study-notes \
  https://github.com/AManHasNoName12138/codex.git "$CD_SOURCE"
"$CD_LAUNCHER" --exec "$CD_GIT" -C "$CD_SOURCE" switch -c source-dev "$CD_PIN"
cd "$CD_SOURCE"
"$CD_LAUNCHER" --exec "$CD_CARGO" fetch --locked --manifest-path "$CD_SOURCE/codex-rs/Cargo.toml"

CD_SHORT="$(git rev-parse --short=12 HEAD)"
CD_BUILD_ID="0.0.0-local.$(date -u +%Y%m%d%H%M%S).g$CD_SHORT"
CD_RELEASE="$CD_ROOT/releases/$CD_BUILD_ID"
test ! -e "$CD_RELEASE"
"$CD_LAUNCHER" --exec "$CD_PYTHON" "$CD_SOURCE/scripts/build_codex_package.py" \
  --variant codex --target "$CD_TARGET" --cargo "$CD_CARGO" \
  --cargo-profile dev-small --package-version "$CD_BUILD_ID" \
  --package-dir "$CD_RELEASE"
git diff --exit-code -- codex-rs/Cargo.lock
```

构建时 `CODEX_REPO_ROOT` 固定为 `ROOT/workspaces/codex`，`CARGO_TARGET_DIR` 为 `ROOT/target`；启动器还从该 clone 读取 HEAD 设置 `STABLE_GIT_COMMIT`，供当前[构建信息模块](../codex-rs/build-info/src/lib.rs)记录来源。构建目录的源码可以修改，编号仍需同时记录未提交差异。

若需要优化版本，将 `--cargo-profile dev-small` 改为 `--cargo-profile release`，并生成新编号。`dev-small` 不适合需要完整调试符号的场景，可按需改用 `dev`。不为日常构建添加 `--all-features`。初次构建和 V8 下载可能耗时较长，磁盘使用也显著高于程序包大小。

**不使用 `--force` 指向已有 release，更不能指向数据目录。** 打包器的 `--force` 会递归替换非空输出目录；本文每次使用全新目录，避免破坏旧程序或状态。

### 4.4 记录产物并切换启动指针

```bash
git rev-parse HEAD > "$CD_RELEASE/source-commit.txt"
git diff --binary --output="$CD_RELEASE/source-changes.patch" HEAD --
git status --short > "$CD_RELEASE/source-status.txt"
(
  cd "$CD_RELEASE"
  find bin codex-resources codex-path -type f -print0 \
    | sort -z | xargs -0 sha256sum > binaries.sha256
)
printf '%s\n' "$CD_BUILD_ID" > "$CD_ROOT/current.txt.new"
mv -f "$CD_ROOT/current.txt.new" "$CD_ROOT/current.txt"
"$CD_LAUNCHER" --paths
"$CD_LAUNCHER" --version
"$CD_LAUNCHER" --exec "$CD_GIT" -C "$CD_ROOT/workspaces/scratch" init
```

补丁只记录已跟踪文件；需要完整复现时，应先提交源码修改，包括新增文件。不要将密钥写入源码或补丁。脚本会保留整个包的内部布局；官方安装器、`npm install -g`、`cargo install --path ...` 和 `codex update` 都不是这里的源码版安装步骤。

### 4.5 独立登录与第一次使用

```bash
"$CD_LAUNCHER" login status || true
"$CD_LAUNCHER" login --device-auth
"$CD_LAUNCHER" tui
"$CD_LAUNCHER" --project "$CD_SOURCE" tui
```

第一次 `login status` 应表示开发目录尚未登录，可能返回非零状态；在启用 `set -e` 的批处理脚本中单独运行或显式处理该预期退出码。`--device-auth` 避免两套浏览器回调服务争用登录端口；若账号不允许设备登录，使用 `login` 并按终端提示完成独立登录，不同时启动两套登录流程。

先在 `scratch` 做简单回合，再进入源码 clone。原来的项目请另行 clone 到 `ROOT/workspaces/<项目>`；不要用指向官方项目的链接或共享 Git worktree。检查项目的 `.codex`、`.agents`、`AGENTS.md`、MCP、hooks 和绝对路径设置，确认仅使用开发资源。

## 5. Windows：原生源码构建与安装

### 5.1 环境准备

本文采用 Windows 11 x64、MSVC 本机目标 `x86_64-pc-windows-msvc`，不使用 MinGW，也不在 WSL 中构建 Windows `.exe`。准备：

- Git for Windows、真实的 Python 3.12+ 安装、PowerShell 7.3+；确认 `python.exe` 不是仅打开 Microsoft Store 的占位入口。
- Visual Studio 2022 或 Build Tools 的 **Desktop development with C++**，包含 x64 MSVC、Windows 10/11 SDK 和 CMake；原生依赖需要时提供 Ninja、NASM、Perl/LLVM 工具。
- 使用 **Developer PowerShell for VS 2022 的 x64 环境**。如果入口启动的是 PowerShell 5.1，在其中运行 `pwsh -NoProfile`，让 PowerShell 7 继承已配置的 `INCLUDE`、`LIB`、`PATH` 等变量。

不需要全局安装 Codex、npm SDK 或 Bazel 来完成本文 Cargo 打包路线。ARM64 的仓库目标存在，但需要相应 MSVC/SDK 和宿主/目标配置；本文未验证 ARM64 原生构建，不将其当作 x64 步骤的无条件替换。

### 5.2 创建独立安装目录

从包含本手册的仓库中打开上述 PowerShell 环境。命令在同一个会话中依次运行：

```powershell
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion -lt [version]'7.3') { throw '需要 PowerShell 7.3+' }
$PSNativeCommandUseErrorActionPreference = $true
$CdGuideRepo = (git rev-parse --show-toplevel).Trim()
$CdRoot = Join-Path $env:LOCALAPPDATA 'CodexDev'
$CdLauncher = Join-Path $CdRoot 'codex-dev.ps1'
$CdSource = Join-Path $CdRoot 'workspaces/codex'
$CdPin = 'c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee'
$CdTarget = 'x86_64-pc-windows-msvc'
$CdGit = (Get-Command git.exe -CommandType Application).Source
$CdPython = (Get-Command python.exe -CommandType Application).Source
$CdOfficial = (Get-Command codex.exe -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1).Source

& $CdPython --version
Get-Command codex -All -ErrorAction SilentlyContinue | Select-Object CommandType, Source
if (Test-Path -LiteralPath $CdRoot) { throw '初始化要求全新目录；更新见第 7 节' }
New-Item -ItemType Directory -Path $CdRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $CdGuideRepo 'study-notes/scripts/codex-dev.ps1') -Destination $CdLauncher
Copy-Item -LiteralPath (Join-Path $CdGuideRepo 'study-notes/scripts/codex-dev.py') -Destination $CdRoot
& $CdLauncher --init
& $CdLauncher --paths
if ($CdOfficial) {
    & $CdOfficial --version
    Get-FileHash -LiteralPath $CdOfficial -Algorithm SHA256 | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $CdRoot 'official-binary.before.json') -Encoding utf8
}
```

`$CdRoot` 可改成用户独占的短路径，例如 `D:\CodexDev`。在共享目录中使用时先配置仅开发账号、SYSTEM 和必要管理员可访问的 NTFS ACL，因为开发认证文件包含登录材料。不要覆盖 PowerShell 的 `$HOME` 变量。

如果官方版来自 npm、ZIP 或桌面安装，其入口可能是 `.cmd`、`.ps1` 或别处的 `.exe`。按实际安装补全记录，不为配合示例移动原安装。脚本执行策略受限时，按组织规则签名或解除文件阻止；不要修改整机执行策略。

### 5.3 独立 Rust 工具链与源码

```powershell
$CdRustupInit = Join-Path $CdRoot 'downloads/rustup-init.exe'
Invoke-WebRequest 'https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe' -OutFile $CdRustupInit
& $CdLauncher --exec $CdRustupInit -y --no-modify-path --profile minimal --default-toolchain 1.95.0
if ($LASTEXITCODE -ne 0) { throw 'rustup 安装失败' }
$CdCargo = Join-Path $CdRoot 'tools/cargo/bin/cargo.exe'
$CdRustup = Join-Path $CdRoot 'tools/cargo/bin/rustup.exe'
& $CdLauncher --exec $CdRustup component add rustfmt clippy rust-src --toolchain 1.95.0
if ($LASTEXITCODE -ne 0) { throw 'Rust 组件安装失败' }
& $CdLauncher --exec $CdRustup target add $CdTarget --toolchain 1.95.0
if ($LASTEXITCODE -ne 0) { throw '目标安装失败' }
& $CdLauncher --exec $CdCargo --version

& $CdLauncher --exec $CdGit -c core.longpaths=true clone --branch study-notes `
    https://github.com/AManHasNoName12138/codex.git $CdSource
if ($LASTEXITCODE -ne 0) { throw 'clone 失败' }
& $CdLauncher --exec $CdGit -C $CdSource config core.longpaths true
& $CdLauncher --exec $CdGit -C $CdSource switch -c source-dev $CdPin
if ($LASTEXITCODE -ne 0) { throw '源码快照切换失败' }
Set-Location -LiteralPath $CdSource
& $CdLauncher --exec $CdCargo fetch --locked --manifest-path (Join-Path $CdSource 'codex-rs/Cargo.toml')
if ($LASTEXITCODE -ne 0) { throw '依赖获取失败' }
```

`core.longpaths` 仅对该次 clone 和开发 clone 设置，不修改用户全局 Git 配置。Python 和 MSVC 使用已安装工具，Rust 和 Cargo 下载/缓存位于开发根目录。

### 5.4 构建并记录完整包

```powershell
$CdShort = (& $CdGit -C $CdSource rev-parse --short=12 HEAD).Trim()
$CdStamp = [DateTime]::UtcNow.ToString('yyyyMMddHHmmss')
$CdBuildId = "0.0.0-local.$CdStamp.g$CdShort"
$CdRelease = Join-Path $CdRoot "releases/$CdBuildId"
if (Test-Path -LiteralPath $CdRelease) { throw '构建目录已存在，请生成新编号' }
& $CdLauncher --exec $CdPython (Join-Path $CdSource 'scripts/build_codex_package.py') `
    --variant codex --target $CdTarget --cargo $CdCargo `
    --cargo-profile dev-small --package-version $CdBuildId --package-dir $CdRelease
if ($LASTEXITCODE -ne 0) { throw '构建或打包失败，不切换 current.txt' }
& $CdGit -C $CdSource diff --exit-code -- codex-rs/Cargo.lock
if ($LASTEXITCODE -ne 0) { throw 'lockfile 已变化，请核对依赖和源码版本' }

& $CdGit -C $CdSource rev-parse HEAD |
    Set-Content -LiteralPath (Join-Path $CdRelease 'source-commit.txt') -Encoding utf8
& $CdGit -C $CdSource diff --binary "--output=$(Join-Path $CdRelease 'source-changes.patch')" HEAD --
if ($LASTEXITCODE -ne 0) { throw '记录源码补丁失败' }
& $CdGit -C $CdSource status --short |
    Set-Content -LiteralPath (Join-Path $CdRelease 'source-status.txt') -Encoding utf8
Get-ChildItem -LiteralPath (Join-Path $CdRelease 'bin'), (Join-Path $CdRelease 'codex-resources'), (Join-Path $CdRelease 'codex-path') -File -Recurse |
    Get-FileHash -Algorithm SHA256 | ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $CdRelease 'binaries.sha256.json') -Encoding utf8

$CdBuildId | Set-Content -LiteralPath (Join-Path $CdRoot 'current.txt.new') -Encoding utf8
Move-Item -LiteralPath (Join-Path $CdRoot 'current.txt.new') -Destination (Join-Path $CdRoot 'current.txt') -Force
& $CdLauncher --paths
& $CdLauncher --version
& $CdLauncher --exec $CdGit -C (Join-Path $CdRoot 'workspaces/scratch') init
```

Git 用 `--output` 直接写补丁，避免 PowerShell 文本管道改变补丁字节。源码修改中的新增文件仍需提交或另行保存。程序显示的版本/提交受构建信息实现影响，验收同时检查 `codex-package.json`、`source-commit.txt` 和实际进程路径，不仅看一个版本字符串。

### 5.5 独立登录与运行

```powershell
& $CdLauncher login --device-auth
if ($LASTEXITCODE -ne 0) { throw '开发身份登录失败' }
& $CdLauncher tui
& $CdLauncher --project $CdSource tui
```

默认使用 `workspaces/scratch`；源码项目使用独立 clone。不要全局设置 `CODEX_HOME`，不要把开发 `release/bin` 加入用户 PATH，也不要用开发入口覆盖现有 `codex` 函数或别名。

需要测试 Windows 提权沙箱时，切换到独立 Windows VM，在该 VM 的专用配置和启动器中明确调整 `windows.sandbox`；保持其余目录隔离。不要在同机并存实例里手工执行 `codex-windows-sandbox-setup.exe`、卸载共享沙箱账户或服务，也不要让开发版启动官方桌面程序。

## 6. 并存验收：检查实际读写

### 6.1 启动前确认

先检查 `--paths`：开发 `CODEX_HOME`、`CODEX_SQLITE_HOME`、Cargo、Rustup、临时和缓存目录都应位于 ROOT；选中的 release 必须位于 `ROOT/releases/`。Linux 官方入口仍由 `type -a codex` 确认，Windows 用 `Get-Command codex -All` 确认。

在一个**官方程序及其相关后台写入都已暂停的短暂窗口**中做数据快照。先从官方实际配置确定数据、SQLite、日志和完整安装目录；默认 `.codex` 之外的覆盖路径也要纳入。运行中的数据库、后台同步和官方自身更新会造成合法变化，不适合拿来证明开发版是否碰过官方文件。

Linux 示例，对每个实际官方目录重复执行；下面只示范一个数据根目录：

```bash
CD_OFFICIAL_DATA="${CODEX_HOME:-$HOME/.codex}"
test -d "$CD_OFFICIAL_DATA"
(
  cd "$CD_OFFICIAL_DATA"
  find . -type f -print0 | sort -z | xargs -0 -r sha256sum
) > "$CD_ROOT/official-data.before.sha256"
```

Windows 示例，`$CdOfficialData` 必须是官方真实路径，不是启动器 `--paths` 中的开发路径：

```powershell
$CdOfficialData = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
function Get-CdDirectoryHashes([string]$Directory) {
    Get-ChildItem -LiteralPath $Directory -File -Force -Recurse | Sort-Object FullName |
        ForEach-Object {
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
            "$hash  $([IO.Path]::GetRelativePath($Directory, $_.FullName))"
        }
}
Get-CdDirectoryHashes $CdOfficialData |
    Set-Content -LiteralPath (Join-Path $CdRoot 'official-data.before.sha256') -Encoding utf8
```

不要把这些清单上传到公共仓库；它们会包含本机路径。符号链接指向的外部数据需要单独快照，不能只遍历链接所在目录。

### 6.2 在开发版做一次独立操作

1. 新初始化目录先检查登录状态；它应没有官方登录和会话。使用独立账号完成 `login`。
2. 在开发 `scratch` 中发起一个简单回合，例如读取该目录并输出一句摘要；确认当前项目、创建的文件和终端进程都属于开发目录。
3. 退出后通过开发 `resume` 确认会话保存在开发目录；如果进行 `logout`/重新登录验证，也始终通过开发入口操作。
4. 查看开发 `codex-home`、`sqlite`、`logs` 的新增文件；运行 `doctor --json` 检查实际配置、状态路径和后台服务信息。`doctor` 会做配置、文件系统和网络等诊断，首次未登录或终端条件不满足时可能返回非零，阅读具体检查项。

```bash
"$CD_LAUNCHER" doctor --json > "$CD_ROOT/doctor-dev.json" || true
"$CD_LAUNCHER" resume
```

Windows 对应执行 `& $CdLauncher doctor --json` 和 `& $CdLauncher resume`；在 `$PSNativeCommandUseErrorActionPreference = $true` 的终端中，诊断非零状态会作为错误报告，应单独运行并查看输出，不把它当作构建失败。

`--paths` 是启动器侧设置；`doctor` 的配置来源、SQLite home 和运行文件路径才用于核对 Codex 实际解析结果。如果发现官方目录、官方远程端点或受管要求将路径指向共享存储，停止同机运行，保留报告并在独立测试环境解决。

### 6.3 比对官方目录并同时使用

开发操作结束、官方仍保持暂停时，再生成 `after` 清单。

```bash
(
  cd "$CD_OFFICIAL_DATA"
  find . -type f -print0 | sort -z | xargs -0 -r sha256sum
) > "$CD_ROOT/official-data.after.sha256"
diff -u "$CD_ROOT/official-data.before.sha256" "$CD_ROOT/official-data.after.sha256"
if [[ -f "$CD_ROOT/official-binary.before.sha256" ]]; then
  sha256sum -c "$CD_ROOT/official-binary.before.sha256"
fi
```

```powershell
Get-CdDirectoryHashes $CdOfficialData |
    Set-Content -LiteralPath (Join-Path $CdRoot 'official-data.after.sha256') -Encoding utf8
$CdDifference = Compare-Object `
    (Get-Content -LiteralPath (Join-Path $CdRoot 'official-data.before.sha256')) `
    (Get-Content -LiteralPath (Join-Path $CdRoot 'official-data.after.sha256'))
if ($CdDifference) { $CdDifference; throw '官方目录发生变化，先调查原因' }
if ($CdOfficial) {
    $CdBefore = Get-Content -LiteralPath (Join-Path $CdRoot 'official-binary.before.json') -Raw | ConvertFrom-Json
    if ((Get-FileHash -LiteralPath $CdOfficial -Algorithm SHA256).Hash -ne $CdBefore.Hash) {
        throw '官方入口文件发生变化'
    }
}
```

同样比较官方外置 SQLite、日志和完整安装目录。清单应无差异；若出现变化，先定位哪个进程写入，不以“已经设置环境变量”为由忽略结果。

然后分别恢复官方和开发 TUI：官方仍能访问原来的本地会话和登录；开发版只看到开发数据。Linux 可用 `ps` 配合 `/proc/<PID>/exe`、`/proc/<PID>/cwd` 检查路径；Windows 可用任务管理器“命令行”列或 `Get-CimInstance Win32_Process` 检查 `ExecutablePath`。只停止自己启动的开发实例及其子进程，不使用 `pkill codex` 或 `taskkill /IM codex.exe /F`。

这组快照验证的是测试窗口内的文件内容，不是对任意未来代码的访问控制证明。严格隔离仍由独立 VM、独立磁盘/项目和测试身份提供。

## 7. 日常开发、更新、回退与移除

### 7.1 修改与验证

源码只在 `ROOT/workspaces/codex` 的 `source-dev` 分支中修改。相关检查也通过 `--exec` 使用开发 Cargo 与缓存。例如 Linux 在 `codex-rs` 中执行：

```bash
cd "$CD_SOURCE/codex-rs"
"$CD_LAUNCHER" --exec "$CD_CARGO" fmt --all -- --check
"$CD_LAUNCHER" --exec "$CD_CARGO" clippy -p codex-tui --tests
"$CD_LAUNCHER" --exec "$CD_CARGO" test -p codex-tui
```

Windows 对应使用 `Set-Location (Join-Path $CdSource 'codex-rs')` 和 `& $CdLauncher --exec $CdCargo ...`。这些是选择受影响 crate 的示例，不代表一次修改只需这三个检查。完整开发命令见 [justfile](../justfile)和[安装文档](../docs/install.md)；`just test` 需要 `cargo-nextest`，`just fmt` 还可能使用 DotSlash 等工具。若要安装这些工具，也通过开发 Cargo 安装，避免改变原来的用户工具目录。

Cargo 测试运行的是新构建代码；与 `--exec` 一样，它不具备运行入口的误配置拦截。修改沙箱、安装、路径处理或执行外部命令的代码时，在独立 VM 内运行测试。此处不建议为调试绕过沙箱或给开发源码提升宿主机管理员权限。

### 7.2 更新与回退

跟进代码时，在开发 clone 中 fetch、审查并切换到明确提交，再重复构建步骤生成新编号和完整新 release。启动器不会运行 `codex update`，源码版更新统一重新构建。可保留旧 release，不覆盖其正在使用的 `.exe`。

退出开发会话后，将 `current.txt` 改为旧目录名即可回退**程序**；父终端和官方入口保持原状。Windows 不覆盖运行中的 `.exe`，也不按程序名批量杀进程。

**程序回退不等于数据库回退。** 新源码可能迁移开发数据库或会话格式。试用有数据格式变化的版本前，先退出开发实例并整体备份 `codex-home`、`sqlite`、`logs`；需要回退数据时恢复同一时点的整组备份，或初始化另一个全新的 ROOT。不要只复制正在使用的 `.sqlite` 文件而漏掉 WAL/SHM，也不要把开发数据库导回官方目录。

### 7.3 移除开发版

先退出该 ROOT 启动的开发进程，保存需要的源码提交和开发数据；核对 `.codex-dev-root`、`--paths` 和目录内容后，再删除这个开发根目录。源码版安装不需要调用官方卸载器，不删除官方 `~/.codex`，也不卸载 Windows 的共享沙箱账户、服务或桌面程序。若曾为开发入口增加独立快捷方式，只删除该快捷方式。

## 8. 常见问题与本手册验证范围

| 现象                                        | 处理方式                                                                                             |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `CODEX_HOME` 不存在，或启动器提示未初始化   | 先在全新 ROOT 复制两个入口文件，再执行 `--init`；不要把它指向官方目录。                              |
| 启动器拒绝共享系统配置/RequirementsPath     | 保留系统策略，改用经授权的独立测试 VM；不通过删除组织配置来启动。                                    |
| `link.exe`、Windows SDK 或头文件缺失        | 使用 x64 MSVC Developer PowerShell，并检查 C++ 工作负载、SDK 和继承的 INCLUDE/LIB。                  |
| Linux `libcap` / ALSA / OpenSSL 开发库缺失  | 安装对应开发包，检查 `pkg-config`；不要跳过完整包的必需资源。                                        |
| V8 或 ripgrep 下载失败、校验不符            | 核对固定提交、网络、代理/CA 和资产可用性；当前脚本使用版本匹配和哈希校验，不关闭校验或混用另一版本。 |
| 直接打包提示 `CODEX_REPO_ROOT` 未设置       | 使用本文 `--exec`；源码应位于约定的 `ROOT/workspaces/codex`。                                        |
| `.exe` 被占用、旧程序仍在运行               | 使用新 release 目录；退出确切的开发实例后切换 `current.txt`。                                        |
| 开发版出现官方会话或官方凭据                | 立即检查真实路径、链接、profile、项目/系统/云端配置及远程端点，再重新做第 6 节验收。                 |
| Git 签名、凭据 helper 或 SSH 登录不可用     | 启动器不继承原用户 Git 配置和 agent；使用公开 HTTPS clone，按需在开发身份下配置，不复用官方凭据。    |
| Windows 改了 USERPROFILE 仍读到真实用户资源 | OS 已知目录不一定读取该变量；使用独立开发账号或 VM，严格隔离采用 VM。                                |

本手册编写时已完成：固定源码机制核对、Linux Bash 语法检查，以及 Python 启动器的 11 项自动检查。检查使用临时官方目录哨兵和模拟程序，实际验证了子进程路径、父环境不变、参数/退出码传递、官方文件未改动，以及共享配置、链接和覆盖参数的拒绝行为。测试中的系统配置位置被替换为临时夹具；生产启动器没有绕过系统配置检查的开关。

复核命令（仓库根目录）：

```bash
python3 -B -m unittest discover -s study-notes/scripts -p 'test_codex_dev.py' -v
bash -n study-notes/scripts/codex-dev.sh
```

**未在本文生成环境完成 Linux 全量 Rust 编译、原生 Windows 编译或真实账号登录/沙箱运行。** Windows 环境映射已做逻辑测试，不能代替 Windows 主机验收。使用者应在自己的目标系统完成第 4–6 节，并记录构建提交、产物哈希和隔离验收结果后再投入日常使用。

## 9. 源码阅读索引

| 主题                               | 当前仓库入口                                                                                                                                                                                 |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Rust 版本与构建 profile            | [rust-toolchain.toml](../codex-rs/rust-toolchain.toml)、[Cargo.toml](../codex-rs/Cargo.toml)                                                                                                 |
| 打包参数、目标与完整性检查         | [cli.py](../scripts/codex_package/cli.py)、[targets.py](../scripts/codex_package/targets.py)、[cargo.py](../scripts/codex_package/cargo.py)、[layout.py](../scripts/codex_package/layout.py) |
| V8 与 ripgrep 下载                 | [v8.py](../scripts/codex_package/v8.py)、[ripgrep.py](../scripts/codex_package/ripgrep.py)                                                                                                   |
| 默认 home、dotenv、临时 helper     | [home-dir/lib.rs](../codex-rs/utils/home-dir/src/lib.rs)、[arg0/lib.rs](../codex-rs/arg0/src/lib.rs)                                                                                         |
| SQLite、日志和凭据存储选择         | [core/config/mod.rs](../codex-rs/core/src/config/mod.rs)、[config_toml.rs](../codex-rs/config/src/config_toml.rs)                                                                            |
| 系统与项目配置优先级               | [loader/README.md](../codex-rs/config/src/loader/README.md)、[loader/mod.rs](../codex-rs/config/src/loader/mod.rs)                                                                           |
| 认证文件与钥匙串命名               | [auth/storage.rs](../codex-rs/login/src/auth/storage.rs)、[auth/storage_file.rs](../codex-rs/login/src/auth/storage_file.rs)、[secrets/lib.rs](../codex-rs/secrets/src/lib.rs)               |
| MCP OAuth 与技能路径               | [rmcp-client/oauth.rs](../codex-rs/rmcp-client/src/oauth.rs)、[skills/host_roots.rs](../codex-rs/ext/skills/src/host_roots.rs)                                                               |
| app-server daemon 的 home 派生路径 | [app-server-daemon/lib.rs](../codex-rs/app-server-daemon/src/lib.rs)                                                                                                                         |
| Windows 沙箱模式、账户与服务       | [config/types.rs](../codex-rs/config/src/types.rs)、[setup.rs](../codex-rs/windows-sandbox-rs/src/setup.rs)、[service_identity.rs](../codex-rs/windows-sandbox-rs/src/service_identity.rs)   |
| 更新检查与诊断                     | [tui/updates.rs](../codex-rs/tui/src/updates.rs)、[cli/doctor.rs](../codex-rs/cli/src/doctor.rs)                                                                                             |
| 项目整体架构                       | [新成员上手指南](../docs/UA_ONBOARDING.md)、[知识图谱](../.ua/README.md)                                                                                                                     |

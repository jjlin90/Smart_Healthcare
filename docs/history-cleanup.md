# 历史个人信息清理与协作说明

## 2026-10-11 姓名与提交邮箱清理

本阶段改写 26 个提交，移除历史文档中的真实姓名路径及作者/提交者私人邮箱；保留公开账号别名。清理后的提交为 `5e29c68`，已用绑定旧远端 SHA 的 `--force-with-lease` 更新 main。清理前后最新应用文件树一致，全部本地 Git 对象（含不可达对象及工具快照引用）复查未命中核对的个人标识。该结论描述本次核对，不代替今后提交的检查。

**旧 SHA 与证据的关系。** 历史改写会改变提交 SHA，原始报告的 `head`、`head_after` 和文件名继续保留当时的值；旧 SHA 在当前克隆中无法解析，不意味着报告被重新生成。仓库随附 [git-history-rewrite-map.json](git-history-rewrite-map.json)，只含 26 组完整旧新 SHA 及范围说明，不含邮箱、姓名或私有文件路径。常用历史标识如下，完整值以 JSON 为准：

| 改写前 SHA | 当前历史中的对应 SHA |
| --- | --- |
| e1304e2 | 7dc37d2 |
| d97d3a5 | 772ff62 |
| 46c59bd | fadf46f |
| a9a4cac | 2e114a3 |
| 43ce1cd | e1438b1 |
| 6f8b01d | 0b45128 |
| 46afedc | 6fdafd5 |

映射不是旧原始字节的副本，也不证明改写前后的每个提交字节相同。不要为了让旧报告中的 SHA 可解析而将原历史重新合并到 main。此映射只覆盖这次 26 个提交的改写；后续普通提交不属于旧新映射。

**仓库外恢复备份。** 本机备份实际位于以下目录，不在项目的 `artifacts/`、Desktop 或系统 Temp 内；仅记录目录规则，不公开个人用户目录：

```powershell
$backupRoot = Join-Path $env:LOCALAPPDATA 'CodexPrivacyBackups/Smart_Healthcare-20261011-114023'
git bundle verify (Join-Path $backupRoot 'before-cleanup.bundle')
git bundle verify (Join-Path $backupRoot 'before-history-rewrite.bundle')
```

`before-cleanup.bundle` 保存清理开始时的所有引用；`before-history-rewrite.bundle` 还包含改写前最后一个干净提交。两份均通过完整 bundle 校验，后者已在仓库外临时裸仓库中实际 fetch，26 个原提交全部可解析；核对后的 26 个新提交在当前仓库也全部可解析。临时恢复仓库在核对后清理，bundle 继续保留。`restore_verification.json` 保存备份哈希和恢复结果；原未提交差异、桌面文件及配置备份也保存在同一私有目录。当前 Git 对象库和 reflog 不再承担旧个人信息的恢复功能；恢复时应使用该私有 bundle，不能公开它或把它拉回发布仓库。

**后续提交邮箱。** 当前仓库的本地配置和 Git 全局默认 `user.email` 均使用 `jjlin90@users.noreply.github.com`。此前全局私人邮箱已替换，原全局配置仅备份在上述仓库外目录的 `global-gitconfig-before-email-cleanup`。全局默认应用于未单独覆盖邮箱的仓库；其他仓库自身的本地或条件配置仍可能覆盖默认值，可在相应仓库用 `git config --show-origin --get-all user.email` 检查，不将它们视为已逐仓库修改。

**远端边界。** 本次已核对公开 main 与本地清理后的历史一致；没有获得 GitHub 缓存、PR 引用或第三方 fork 中旧对象的删除确认。仅替换分支历史不能据此声称服务器全部旧提交已不可访问。下文的早期 Support 记录属于另一阶段，不作为本次姓名与邮箱清理的服务器确认。

## 先前路径清理（历史记录）

该阶段清理范围为旧版本 `output/` 下的简历 PDF、`scripts/*resume*.py` 简历生成辅助脚本及 `.mimosa/` 运行状态。最新应用代码不依赖这些文件；当前忽略规则与上传前检查继续阻止它们入库。正常提交作者元数据未纳入该次文件清理，后续已在上面的 2026-10-11 阶段单独处理。

### 当时的清理方法与保护措施

1. 将旧历史备份为本地 Git bundle，并单独保存未提交的文档修改。备份含旧个人信息，仅用于本机恢复，禁止上传；私有工作目录 `artifacts/` 已被忽略。
2. 在独立镜像中使用 `git-filter-repo --sensitive-data-removal --invert-paths` 移除上述历史路径，保留应用的提交演进。
3. 比较历史清理前后的最新文件树，确认相同；再加入本轮经验证的文档、评测留档和复现说明改动。
4. 推送时明确限制到 `refs/heads/main`，并使用绑定旧远端提交的 `--force-with-lease`。若远端已变化，推送应拒绝，重新合并后再准备。不能用无条件镜像推送覆盖其他分支或工具内部引用。

### 当时的其他副本说明

提交哈希会变化。旧克隆或其他会话不能再把旧历史合并回 main；先备份各自尚未提交的工作，再使用新历史克隆，或按提交映射迁移自己的改动。CI 和新克隆需复核。

本机 Codex 工具快照和私有 bundle 与远端发布分支是不同的副本；不要把这些内部引用推送到 GitHub。若本地预检发现工具快照中的旧历史，需要核对来源，不能据此自动判断远端 main 清理失败。

该阶段的本机 reflog、不可达 Git 对象及私有 bundle 也可能保留旧个人信息。预检只扫描引用可达历史，并不证明整个本地对象库已清空。当时为保留恢复能力，没有自动过期 reflog 或清除这些备份；普通提交作者元数据也仍保留。此段为早期处理方式的历史说明，当前恢复来源及作者邮箱处理以本页 2026-10-11 节为准。

先前记录记载 GitHub Support 工单 #4804971 于 2026-09-29 回复确认执行该阶段的缓存清理和垃圾回收，并标记解决；回复称当时受影响的旧提交链接应返回 404。本次未重新核验该工单或旧链接，该历史记录不涵盖此次姓名/邮箱改写、本机或第三方副本。

强制更新分支后，GitHub 缓存、旧提交链接、PR 引用、fork 或第三方已下载的副本仍可能保留旧内容。需核查远端引用和 fork；符合 GitHub 支持条件的个人敏感信息，可向 GitHub Support 提供仓库名、首个被改写提交、受影响 PR 和清理结果，申请处理缓存和不可写引用。不能承诺仅靠一次推送就删除他人已持有的副本。

仅发现个人联系方式不能证明 API 凭据泄露；若扫描确认实际密钥暴露，则另行撤销或轮换对应凭据。

官方流程：[GitHub 敏感数据清理说明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)。

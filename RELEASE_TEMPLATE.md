# Release 发布说明模板

> **怎么用**：发版时复制下面 `---` 之间的内容，替换掉 `{{...}}` 占位符，
> 贴进 Gitee / GitHub 的 Release 说明框。
>
> 同时把 `dist/` 里生成的 zip 上传到该 Release 的**附件（Assets）**里。
>
> ⚠️ **产物不要提交进 git 仓库** —— 二进制会让仓库永久膨胀，clone 变慢。
> Release 附件才是放编译产物的地方。

---

## 先确认清单

发版前过一遍：

```
□ 代码已提交并推送（git status 干净）
□ 服务层测试全过（pytest server/tests -q）
□ 便携版重新组装过（python build_portable.py --clean）
□ 在 Windows 上实测：解压 → 启动.bat → 浏览器打开 → 批一次题
□ 版本号改过（build_portable.py 里的 APP_VERSION）
□ dist/ 里的 zip 是刚生成的（不是上次的）
```

**版本号约定**（跟着感觉走，但别乱）：

```
v1.0.0   第一次能用的版本
v1.0.1   修 bug
v1.1.0   加了功能 / 加了题型
v2.0.0   改了不兼容的东西（比如接口格式）
```

---

<!-- ══════════ 下面是模板正文，复制这一段 ══════════ -->

# {{v1.0.0}}

{{一句话说清这版最重要的变化。}}

## 下载

| 文件 | 说明 |
|---|---|
| `shenlun-{{1.0.0}}-win-x64-portable.zip` | **推荐**。解压即用，不用装 Python |
| `shenlun-{{1.0.0}}-win-x64.exe` | 单文件版。双击就能跑，但启动慢一些，也可能被杀毒误报 |

> 选哪个？**先试便携版。** 它不容易被杀毒软件拦下来。

## 怎么用

```
① 把 zip 整个解压出来（别在压缩包里直接双击）
② 双击 启动.bat
③ 浏览器会自动打开 → 右上角「设置」里填 DeepSeek API Key
④ 贴题干 + 材料 + 机构参考答案，再贴考生答卷，点「开始批改」
```

**需要联网**（批改要调用 DeepSeek）。Key 只存在你自己电脑上，不会上传。

## 这版有什么

- {{新功能 / 修的问题}}
- {{...}}

## 已知问题

- {{还没解决但你知道的问题}}
- **准确度尚未系统测量** —— 拿它当逐点检查表用，别把分数当权威

## 校验（可选，但建议留）

```
SHA256  shenlun-{{1.0.0}}-win-x64-portable.zip
        {{把 certutil -hashfile 文件名 SHA256 的结果粘这里}}
```

Windows 上验证：

```cmd
certutil -hashfile shenlun-1.0.0-win-x64-portable.zip SHA256
```

---

**完整说明见 [README]({{README 的链接}})。有问题直接提 Issue。**

<!-- ══════════ 模板正文结束 ══════════ -->

---

# 附：发 Release 的操作步骤

## Gitee

```
① 网页进仓库 → 「发行版」→「创建发行版」
② 标签：v1.0.0（会自动创建这个 tag）
③ 标题：v1.0.0
④ 说明：粘贴上面的模板正文
⑤ 附件：把 dist/*.zip 拖进去
⑥ 发布
```

## GitHub

```bash
# 打 tag 并推上去
git tag -a v1.0.0 -m "v1.0.0"
git push origin v1.0.0
```

然后网页 → Releases → Draft a new release → 选刚推的 tag → 粘说明 → 传附件。

## 第一次发之前要改的地方

```
□ README.md 里的 Releases 链接指向正确的仓库
□ build_portable.py 里的 APP_VERSION
□ 使用说明.txt 里的版本号（build_portable.py 会自动填）
```

---

# 附：怎么算 SHA256

```bash
# Linux / macOS
sha256sum dist/shenlun-1.0.0-win-x64-portable.zip

# Windows PowerShell
Get-FileHash dist\shenlun-1.0.0-win-x64-portable.zip -Algorithm SHA256
```

**为什么值得做**：朋友下载后能自己核对文件没损坏、没被中途替换。
成本几乎为零，但显得靠谱。

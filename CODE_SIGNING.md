# Code signing policy

## 当前状态

Subtitle Studio 正在准备申请 SignPath Foundation 免费开源签名，**尚未获批，现有程序和安装包仍未签名**。本政策不代表基金会已接受或支持本项目。有效数字签名也不保证新版本立即免除 Windows SmartScreen 提示。

申请获批并实际启用后，将使用以下署名：

> Free code signing provided by SignPath.io, certificate by SignPath Foundation

## 职责

作者署名：**TuseCreation**。用户已于 2026-10-16 确认，由仓库账号 [StuTuse](https://github.com/StuTuse) 承担提交者、外部贡献审查者和签名审批者，并确认 GitHub 已开启双重验证。新增成员须公开更新此处的职责。所有具备相关权限的成员须为源码托管和 SignPath 启用多因素认证；SignPath 账号创建后仍须独立核验。

外部贡献须经过维护者审查。每次发布签名必须由获授权审批者人工批准；修改构建脚本、工作流和依赖时同样需要审查。仅对本项目维护的源码所生成的项目产物申请签名；不使用项目签名身份重新签署第三方 FFmpeg、Qt、Python 或其他上游二进制。

## 构建和发布约定

1. 签名输入须来自可核验的源码提交和受控自动构建；记录提交、版本、构建日志、产物散列及依赖来源。
2. 应用和安装器版本须与根目录 `VERSION` 对齐。只在签名前完成所有二进制修改。
3. 主程序先签名，再封装安装器；安装器和卸载程序的签名方式须与实际 SignPath/Inno Setup 集成一起验收。只签最外层安装包不等于内部程序已签名。
4. 下载签名产物后，核验 Authenticode 信任链、签名者、时间戳和文件版本，并进行安装、启动、卸载及桌面快捷方式验收。不得将未签名产物标为已签名版本。
5. 发行页面须链接本政策、[隐私政策](PRIVACY.md)、[许可说明](COPYING.md)及对应源码。

目前只有测试工作流，尚未建立 SignPath 可验证的发布构建；不得将本机历史构建上传后直接宣称满足来源验证。未配置签名服务 ID、凭据或生产签名工作流。v2.1.7 增加安装阶段隐私展示和联网检查选择，仍须在分发前完成完整来源及依赖审查。

## 参考和申请

- [SignPath Foundation 条件](https://signpath.org/terms.html)
- [申请入口](https://signpath.org/apply.html)
- [本项目申请准备单](docs/SIGNING_APPLICATION.md)

隐私行为以 [PRIVACY.md](PRIVACY.md) 为准。对于默认更新检查，不使用「无用户请求绝不联网」的承诺。

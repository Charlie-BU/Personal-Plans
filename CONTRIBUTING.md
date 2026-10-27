# Contributing / 参与贡献

Chinese and English issues and pull requests are welcome. Describe the concrete problem and the smallest change that solves it. Use synthetic data only; never submit private repositories, real plans, tokens, messages, or personal screenshots.

欢迎中英文 issue 和 PR。说明具体问题和最小修复，只用合成数据；不要提交真实个人计划、私有仓库内容、令牌、消息或私人截图。

Run from the repository root / 从仓库根目录运行：

```sh
python3 -m pip install -r scripts/requirements.txt
python3 -B -m unittest discover -s scripts -p 'test_*.py' -v
python3 -B scripts/demo.py
```

Keep changes scoped. Preserve `plans/` ownership, source provenance, unknown outcomes, hash validation, atomic commits, and concurrency protection. Update the relevant references and add a meaningful regression test for changed behavior. Explain any data-format change and its explicit migration path; do not silently rewrite personal history.

保持改动范围小，保留目录边界、来源、未知状态、哈希校验、原子提交和并发保护。行为变化补回归测试与相关说明；数据格式变化明确迁移方式，不悄悄改写个人历史。

Contributions are provided under this project's MIT license. / 贡献按本项目 MIT 许可证提供。

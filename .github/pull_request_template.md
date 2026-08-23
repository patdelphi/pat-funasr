# 变更说明

请简述本次修改的目标、范围和用户可见影响。

## 检查清单

- [ ] 未修改无关代码，未覆盖已有工作区修改。
- [ ] 新功能已先补测试；修复已有测试或复现证据。
- [ ] 所有 API 均有异常处理；数据库操作使用事务。
- [ ] 未经确认未执行数据库迁移、部署、依赖安装或外部 API 请求。
- [ ] 未经确认未执行 commit、push、pull、merge。
- [ ] `python -m compileall -q app scripts tests` 通过。
- [ ] `python -m pytest -q` 通过，失败和非预期警告为 0。
- [ ] Markdown/CSV 等文本文件为 UTF-8 BOM + CRLF。
- [ ] API schema、WebUI 字段、Docs 文档与实际行为一致。

## 验证结果

填写执行的命令、通过数量、已知限制和未执行事项。

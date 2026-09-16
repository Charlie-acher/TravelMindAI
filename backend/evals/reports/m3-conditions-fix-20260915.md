# M3 固定题库真实评测

题库：documents.v1；题库SHA-256：`7f1f5a895e1732a66c30cd4f136c596b827448f6164214111c6836713a5eff9f`。

模型：deepseek-v4-pro；检索：真实Embedding＋Milvus＋PostgreSQL。

已执行 33 题；成功返回 33 题。

```json
{
  "questions": 33,
  "successful_answers": 33,
  "retrieval_questions": 28,
  "recall_at_5": 1.0,
  "mrr_at_5": 0.9464285714285714,
  "status_correct": 33,
  "facts_covered": 51,
  "facts_total": 51,
  "citation_valid_questions": 28,
  "citation_questions": 28,
  "faithfulness_reviewed": 0,
  "faithfulness_passed": 0,
  "faithfulness_rate": null,
  "search_seconds": {
    "mean": 0.2739570515202076,
    "p95": 0.8776008000131696
  },
  "answer_seconds": {
    "mean": 3.552430351518772,
    "p95": 8.000450000050478
  }
}
```

召回率为有证据题的逐题Recall@5均值；MRR截断到前5名。无证据题不进入召回分母。失败题保留在执行及标注分母。评分使用生产链路最终返回值，包含其有界修复；各次模型原始输出另存，不能把本分数称为未经修复的模型准确率。引用有效率只检查编号范围；事实覆盖只检查预先标注词组。全文忠实度必须另读每个断言与引用原文，未审查时为null，不用另一个模型自动判分。

本题库由Agent阅读固定来源并人工式标注，不是外部人员独立盲测；小规模静态检索不能代表全量资料、实时旅行信息或生产吞吐。未调用网页搜索、高德，未计算账单费用。LvBanGPT PDF许可未确认，相关摘录仅供本地研究，发布前须确认。

清理状态：{"milvus_collection_removed": true, "postgres_schema_removed": true, "temporary_files_removed": true}。完整出处、配置/提示词/源码hash、检索原文、模型原始输出和逐题评分见同名JSON。

# 書き換え例

このファイルは、`SKILL.md` の代表例だけでは判断しづらい場合に読む。

目的は、表現を磨くだけでなく、読者の成果・操作・判断順に情報を戻すこと。

## ドキュメント構造の例

### 内部の保存方式を利用者価値に戻す

書き換え前：

> SQLite event store による append-only な graph event 管理

修正版：

> 取り込んだ文脈をローカル SQLite に追記履歴として保存します。過去の記録を上書きせず、あとから状態を確認できます。

### 内部処理の羅列を作業場面に戻す

書き換え前：

> task text に基づく context search / ranking / subgraph summary

修正版：

> `pcg start "<task>"` で、今回の作業に関係する要件、制約、過去の判断、候補ファイル、検証コマンドを確認できます。

### チェック機能を確認できることへ戻す

書き換え前：

> secret、constraint、non-goal、decision conflict、evidence、hash chain、manifest を確認する guard / validate

修正版：

> 作業後に、秘密情報の混入、制約違反、過去の判断との衝突、根拠不足、export の不整合を確認できます。

### 連携部品を利用シーンへ戻す

書き換え前：

> Codex skill template、Codex App Server runtime glue、hooks、policy check

修正版：

> Codex と連携して作業するための設定、hooks、policy check を生成・利用できます。

## 根拠不足の例

書き換え前：

> 山田健診センターは1989年に設立され、地域医療において重要な役割を担い、住民の健康維持に欠かせない存在として、その意義は非常に大きいと言えるでしょう。

修正版：

> 山田健診センターは1989年に設立された。

補足：

- 「重要な役割」「欠かせない存在」は根拠が本文にないため削る。
- 検診件数や地域内での位置づけを入れる場合は、原文または参照資料にある事実だけを使う。

## repoドキュメントの例

書き換え前：

> 本プロジェクトにおいては、Project / Goal / PlanVersion / Task / Artifact / AgentRun / Evaluation / ValidationReport / Decision を保存し、PlanVersion を immutable に扱い、再計画は new version と supersede で表現します。Codex などの外部エージェントは MCP 経由で ready task を claim し、成果物と完了結果を登録します。

修正版：

> このプロジェクトは、AIエージェントの作業をタスクグラフとして記録する。
>
> 目標、計画、タスク、成果物、判断の履歴を残し、計画を後から上書きせずに変更前後の流れを追えるようにする。
>
> Web UI では計画と進捗を確認できる。タスクの実行や完了記録は、Codex などの外部エージェントが agentflow に登録する。

## 未確認の仕様断定の例

書き換え前：

> このCLIオプションを使うと、すべての環境で自動的に設定ファイルが生成されます。

修正版：

> このCLIオプションで設定ファイルを生成できるかは、参照資料では確認できていない。

補足：

- 対応バージョン、対象環境、自動生成の条件が確認できないため、断定を避ける。
- 公式資料またはrepo内で確認できた場合だけ、利用条件を本文に戻す。

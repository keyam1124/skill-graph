# SkillGraph Cartographer

`SkillGraph Cartographer` は、リポジトリ内の `SKILL.md` を読み取り、スキル同士の参照関係をローカルビューアで確認するためのツールです。

現在の実装は、ファイルパス、Markdown リンク、本文中のスキル名参照などをもとにした決定論的な解析が中心です。Codex や Claude Code による `SKILL.md` の意味分析は、この実装にはまだ含まれていません。

## 現在できること

- リポジトリ内の `SKILL.md` を収集する
- Markdown リンクや本文中の参照から、スキル同士の関係を抽出する
- 解決できない参照や、どこからも参照されていないスキルを検出する
- 収集した関係をローカルビューアで表示する
- エージェントが推論した一時的な注釈を、ビューア表示用に重ねる

収集と表示は読み取り専用です。対象リポジトリ内のスキル定義や設定ファイルは変更しません。

## クイックスタート

このリポジトリ自身を対象に、関係図をローカルビューアで開きます。

```bash
python3 scripts/skillgraph.py collect . \
  | python3 scripts/skillgraph.py view --stdin --no-open
```

コマンドはビューアの URL を出力します。ブラウザでその URL を開くと、収集したスキルと参照関係を確認できます。

JSON だけを確認する場合は、`collect` を単体で実行します。

```bash
python3 scripts/skillgraph.py collect .
```

## 使い方

別のリポジトリを調べる場合は、`collect` に対象リポジトリのパスを渡します。

```bash
python3 scripts/skillgraph.py collect /path/to/repo \
  | python3 scripts/skillgraph.py view --stdin --no-open
```

Skill 内に同梱されている wrapper からも実行できます。

```bash
SKILLGRAPH_REPO_ROOT=/path/to/repo \
  skills/meta/skillgraph-cartographer/scripts/view-skillgraph.sh --no-open
```

`view` では、必要に応じてホストやポートを指定できます。

```bash
python3 scripts/skillgraph.py view --stdin --host 127.0.0.1 --port 0 --no-open
```

## ビューアで確認できること

ビューアでは、次の情報を確認できます。

- 収集されたスキル
- スキル同士の参照関係
- 参照関係の根拠になったファイルやテキスト
- 解決できない参照
- どこからも参照されていないスキル
- エージェントが一時的に追加したラベル、カテゴリ、関係の補足

決定論的な解析結果と、エージェントが推論した補足情報は区別して扱います。推論による補足は表示用の注釈であり、`SKILL.md` の正本ではありません。

## AgentSkill として使う場合

`skills/meta/skillgraph-cartographer/SKILL.md` は、Codex などのエージェントが SkillGraph を調べるための AgentSkill です。

この Skill を使うエージェントは、対象リポジトリに同じスクリプトがあると仮定せず、Skill ディレクトリに同梱された CLI を使います。

基本の流れは次のとおりです。

1. 対象リポジトリを決める
2. 同梱 CLI の `collect` で決定論的な解析結果を取得する
3. 必要に応じて、スキルの役割やカテゴリなどを一時的に推論する
4. 推論結果を表示用の注釈として追加する
5. `view --stdin` でローカルビューアに表示する

## リポジトリ構成

```text
.
├── scripts/
│   ├── skillgraph.py
│   └── view-skillgraph.sh
├── skills/
│   └── meta/
│       └── skillgraph-cartographer/
│           ├── SKILL.md
│           └── scripts/
│               ├── skillgraph.py
│               └── view-skillgraph.sh
└── tests/
    └── test_skillgraph.py
```

トップレベルの `scripts/skillgraph.py` は、同梱 CLI への互換エントリポイントです。

## 開発とテスト

ユニットテストは次のコマンドで実行します。

```bash
python3 -m unittest tests/test_skillgraph.py
```

テストでは、主に次の挙動を確認しています。

- `collect` がグラフ情報を標準出力へ出すこと
- 収集処理が対象リポジトリを書き換えないこと
- free-form な `SKILL.md` を扱えること
- エージェントによる一時注釈をビューア用データに反映できること
- 孤立スキルや解決できない参照を診断できること

## 制約

- 主な対象は `SKILL.md` です。
- references、templates、scripts は関係理解の補助情報として扱いますが、ビューア上の主対象にはしません。
- 現時点では、Codex や Claude Code による意味分析は実装範囲に含まれていません。

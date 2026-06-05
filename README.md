# SkillGraph Cartographer

`SkillGraph Cartographer` は、別リポジトリに `gh skill` でインストールして使う Agent Skill です。対象リポジトリ内の `SKILL.md` を読み取り、スキルの全体像、スキル同士のつながり、確認が必要な項目をローカルビューアで表示します。

配布対象は `skills/skillgraph-cartographer/` です。`gh skill` は `skills/*/SKILL.md` を検出するため、このリポジトリを skills repository として扱えます。

## できること

- リポジトリ内の `SKILL.md` を収集する
- Markdown リンクと `SKILL.md` パス参照から、直接書かれている関係を抽出する
- 参照先が見つからない項目を見つける
- 同じ呼び名のスキルを見つける
- 置き場所ごとに内容が違うスキルを見つける
- host agent 用の enrichment template を出し、base graph と enrichment JSON を merge する
- host agent の推論で、表示用のラベル、要約、カテゴリ、クラスタ、AI が読み取った関係を追加する
- `viewSuggestions` をビューアの操作に接続し、推奨ビューをクリックして絞り込む
- 各スキルの詳細で、関係しているスキル、根拠、理由を確認する
- 直接書かれている関係と AI が読み取った関係を区別して表示する

収集と表示は読み取り専用です。対象リポジトリ内のスキル定義や設定ファイルは変更しません。

## ビューアで確認できること

ビューアは次の 4 つの見方に分かれています。

- 全体を把握する: スキル数、つながり数、確認が必要な項目をまとめて確認する
- つながりを確認する: 直接書かれている関係と AI が読み取った関係を見分ける
- 気になる点を確認する: 参照先が見つからない項目、同じ呼び名のスキル、置き場所ごとに内容が違うスキルを確認する
- スキルを探す: スキル名、目的、説明、ファイル名で検索する

## インストール

`gh skill` は GitHub CLI 2.90.0 以降の public preview 機能です。2026-06-03 時点では、Codex など複数の agent の project scope は `.agents/skills` を共有します。

リモートリポジトリから Codex 用に project scope へ入れる場合:

```bash
gh skill install keyam1124/skill-graph skillgraph-cartographer --agent codex --scope project
```

Claude Code 用に user scope へ入れる場合:

```bash
gh skill install keyam1124/skill-graph skillgraph-cartographer --agent claude-code --scope user
```

ローカル checkout から動作確認する場合:

```bash
gh skill install . skillgraph-cartographer --from-local --dir /tmp/skillgraph-cartographer-install
```

インストール前に確認する場合は、対象リポジトリを指定して preview します。

```bash
gh skill preview keyam1124/skill-graph skillgraph-cartographer
```

## 使い方

Skill をインストールした agent に、対象リポジトリで次のように依頼します。

```text
このリポジトリの SkillGraph を表示して。カテゴリと推論エッジも付けて。
```

Skill が有効になると、agent は次の流れで動きます。

1. インストール済み Skill ディレクトリの `scripts/skillgraph.py` を使う
2. `collect` で対象リポジトリの決定論的な SkillGraph JSON を作る
3. `SKILL.md` の本文を読み比べ、host agent の推論で `nodeAnnotations` と `inferredEdges` を追加する
4. `view --stdin` に JSON を渡し、ローカルビューア URL を出力する

直接 CLI を試す場合:

```bash
python3 skills/skillgraph-cartographer/scripts/skillgraph.py collect . \
  | python3 skills/skillgraph-cartographer/scripts/skillgraph.py view --stdin --no-open
```

JSON だけを確認する場合:

```bash
python3 skills/skillgraph-cartographer/scripts/skillgraph.py collect .
```

host agent が読みやすい context を含める場合:

```bash
python3 skills/skillgraph-cartographer/scripts/skillgraph.py collect . \
  --agent-context --max-chars-per-skill 1200
```

任意のリポジトリを調べる場合:

```bash
python3 skills/skillgraph-cartographer/scripts/skillgraph.py collect /path/to/repo \
  | python3 skills/skillgraph-cartographer/scripts/skillgraph.py view --stdin --no-open
```

保存済み JSON を再表示する場合:

```bash
python3 skills/skillgraph-cartographer/scripts/skillgraph.py view \
  --file /tmp/skillgraph.enriched.json --no-open
```

base graph と host agent の enrichment JSON を分けて扱う場合:

```bash
python3 skills/skillgraph-cartographer/scripts/skillgraph.py collect . \
  > /tmp/skillgraph.base.json

python3 skills/skillgraph-cartographer/scripts/skillgraph.py enrichment-template \
  /tmp/skillgraph.base.json > /tmp/skillgraph.agent-input.json

python3 skills/skillgraph-cartographer/scripts/skillgraph.py merge \
  --base /tmp/skillgraph.base.json \
  --annotations /tmp/skillgraph.annotations.json \
  > /tmp/skillgraph.enriched.json
```

ビューアは URL を標準出力へ出します。終了するには、実行中のプロセスを `Ctrl-C` で止めます。
既定では `127.0.0.1` などの loopback host にだけ bind します。非 loopback host に出す場合は、graph JSON が LAN から読める可能性を理解したうえで `--allow-non-loopback` を指定します。

## 推論付きグラフの扱い

CLI は外部 LLM API を呼びません。Codex / Claude Code など、この Skill を読み込んだ host agent が、ローカルに読める `SKILL.md` をもとに推論します。

推論結果は次のフィールドに分けて追加します。

- `nodeAnnotations`: 表示用ラベル、要約、カテゴリ、クラスタ、ロール、トリガー語
- `inferredEdges`: host agent が解釈した Skill 間のリレーション
- `viewSuggestions`: ビューア上で注目しやすいクラスタやフィルタ候補

決定論的な `nodes` と `edges` は正本として扱い、推論結果では上書きしません。
`inferredEdges` は「AI が読み取った関係」として描画され、スキル詳細にも表示されます。
関係がない Skill は正常な状態として扱い、孤立 Skill として診断しません。

## リポジトリ構成

```text
.
├── skills/
│   └── skillgraph-cartographer/
│       ├── SKILL.md
│       └── scripts/
│           ├── skillgraph_core/
│           │   ├── analysis.py
│           │   ├── cli.py
│           │   ├── enrichment.py
│           │   ├── registry.py
│           │   ├── shared.py
│           │   ├── viewer.html
│           │   └── viewer.py
│           ├── skillgraph.py
│           └── view-skillgraph.sh
└── tests/
    └── test_skillgraph.py
```

`skills/skillgraph-cartographer/` が `gh skill` でインストールされる配布単位です。CLI とビューア起動用スクリプトも、この配布単位の中に置いています。

## 開発とテスト

ユニットテスト:

```bash
python3 -m unittest discover -s tests
```

`gh skill` 互換のローカル検証:

```bash
gh skill publish --dry-run
gh skill install . skillgraph-cartographer --from-local --dir /tmp/skillgraph-cartographer-install --force
```

主な検証対象:

- `collect` が graph JSON を標準出力へ出す
- `collect` が対象リポジトリを書き換えない
- free-form な `SKILL.md` を扱える
- 推論注釈をビューア用データへ安全に反映できる
- Markdown link、reference-style link、line range evidence を扱える
- 同じ呼び名のスキルと解決できない参照を診断できる
- 推論された関係を重複 append しない
- 孤立 Skill を問題として診断しない

## 制約

- 主な対象は `SKILL.md` です。
- references、templates、scripts は関係理解の補助情報として扱いますが、ビューア上の主ノードにはしません。
- 推論結果は表示用の注釈であり、対象リポジトリの source of truth ではありません。
- 関係のない Skill が存在しても正常です。
- `gh skill` は public preview のため、CLI のオプションや対応 agent は変更される可能性があります。

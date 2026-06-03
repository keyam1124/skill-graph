# SkillGraph Cartographer

`SkillGraph Cartographer` は、別リポジトリに `gh skill` でインストールして使う Agent Skill です。対象リポジトリ内の `SKILL.md` を読み取り、決定論的な参照関係と、Codex / Claude Code などの host agent が各 Skill を読み比べて解釈した関係を重ねた SkillGraph をローカルビューアで表示します。

配布対象は `skills/skillgraph-cartographer/` です。`gh skill` は `skills/*/SKILL.md` を検出するため、このリポジトリを skills repository として扱えます。

## できること

- リポジトリ内の `SKILL.md` を収集する
- Markdown リンクと `SKILL.md` パス参照から、直接確認できる関係を抽出する
- 解決できない参照と重複 alias を診断する
- host agent の推論で、表示用のラベル、要約、カテゴリ、クラスタ、解釈済みリレーションを追加する
- 各 Skill の詳細で、incoming / outgoing のリレーション、根拠、推論理由を確認する
- 決定論的な関係と推論由来の注釈を区別してローカルビューアに表示する

収集と表示は読み取り専用です。対象リポジトリ内のスキル定義や設定ファイルは変更しません。

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

任意のリポジトリを調べる場合:

```bash
python3 skills/skillgraph-cartographer/scripts/skillgraph.py collect /path/to/repo \
  | python3 skills/skillgraph-cartographer/scripts/skillgraph.py view --stdin --no-open
```

ビューアは URL を標準出力へ出します。終了するには、実行中のプロセスを `Ctrl-C` で止めます。

## 推論付きグラフの扱い

CLI は外部 LLM API を呼びません。Codex / Claude Code など、この Skill を読み込んだ host agent が、ローカルに読める `SKILL.md` をもとに推論します。

推論結果は次のフィールドに分けて追加します。

- `nodeAnnotations`: 表示用ラベル、要約、カテゴリ、クラスタ、ロール、トリガー語
- `inferredEdges`: host agent が解釈した Skill 間のリレーション
- `viewSuggestions`: ビューア上で注目しやすいクラスタやフィルタ候補

決定論的な `nodes` と `edges` は正本として扱い、推論結果では上書きしません。
`inferredEdges` はグラフの edge として描画され、node 詳細の incoming / outgoing リレーションにも表示されます。
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
- 重複 alias と解決できない参照を診断できる
- 孤立 Skill を問題として診断しない

## 制約

- 主な対象は `SKILL.md` です。
- references、templates、scripts は関係理解の補助情報として扱いますが、ビューア上の主ノードにはしません。
- 推論結果は表示用の注釈であり、対象リポジトリの source of truth ではありません。
- 関係のない Skill が存在しても正常です。
- `gh skill` は public preview のため、CLI のオプションや対応 agent は変更される可能性があります。

# 詰めチャレ レーティング分布

「将棋クエスト」の「実戦！詰めチャレ」ランキングを週1回集計し、匿名化した
レーティング分布を GitHub Pages で表示するためのリポジトリです。

## 方針

- 将棋クエストへのアクセスは GitHub Actions の週次バッチだけが行います。
- 公開ファイルにはユーザー名を含めません。
- 全件取得と品質検証に成功した場合だけ `public/data/latest.json` を更新します。
- 失敗時は最後に成功した分布を維持し、`public/data/status.json` の状態だけを更新します。
- 失敗ログは Actions artifact に保存し、GitHub Issue で通知します。

## ローカル確認

Python 3.11 以上を想定しています。追加パッケージは不要です。

```powershell
python -m unittest discover -s tests -v
python -m http.server 8000 -d public
```

ブラウザで `http://localhost:8000` を開いてください。初期状態では実データがないため、
データ未取得の案内が表示されます。

## データ更新

小規模な疎通確認:

```powershell
python scripts/update_data.py --max-pages 2 --dry-run
```

全件更新:

```powershell
python scripts/update_data.py
```

通常は `.github/workflows/update-ranking.yml` の手動実行で先に疎通確認し、その後、
毎週月曜日 03:15（日本時間）のスケジュール実行を利用します。

接続先やプロトコルが変わった場合、ワークフローを失敗させ、公開済みの分布は更新しません。

## 注意

このサイトは非公式です。将棋クエストおよび運営会社とは関係ありません。
ランキング取得には待機時間を設け、サービスへの負荷を抑えています。


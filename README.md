# SPW - Password Manager

ブラウザで使える軽量パスワードマネージャーです。パスワードデータは 7zip (AES-256) で暗号化してローカルに保存します。

- サーバー: [server.py](server.py) (Python 標準ライブラリのみ / 外部依存なし)
- フロントエンド: [public/](public/) (HTML / CSS / JS)
- 保存先: `data/` (GitHub には **アップロードしません**。`.gitignore` で除外)

## 必要要件

- root 権限 (`sudo`)
- `python3`
- `git`
- `p7zip` (7z) ※ 無ければインストーラーが自動インストールします
- `tailscale` (任意。リモートから HTTPS でアクセスしたい場合のみ)

## インストール（GitHub から）

### 方法 1: インストーラースクリプトを直接実行

```bash
curl -fsSL https://raw.githubusercontent.com/hirogura/spw/main/install-spw.sh -o install-spw.sh
sudo bash install-spw.sh
```

### 方法 2: リポジトリを clone してから実行

```bash
git clone https://github.com/hirogura/spw.git /opt/lxd-data/spw
cd /opt/lxd-data/spw
sudo bash install-spw.sh
```

インストーラーは以下のことを行います。

1. `python3` / `git` / `7z` を確認（不足時は自動インストール）
2. GitHub リポジトリ `https://github.com/hirogura/spw.git` を `/opt/lxd-data/spw` に取得・更新
   - 既に Git リポジトリがある場合は `origin/main` に更新
   - 既存のデータディレクトリ `data/` は保持されます
3. `data/backups` ディレクトリを作成
4. systemd サービス `spw.service` を登録・起動
5. Tailscale Serve の設定（tailscale がある場合のみ）

### アクセス URL

インストール完了時に表示されます。例:

- ローカル: `http://<IP>:3345`
- Tailscale HTTPS: `https://<ホスト名>:3344`

ポートは環境変数 `PORT` で変更できます。

```bash
sudo PORT=8080 bash install-spw.sh
```

### 初回アクセス

ブラウザでアクセスし、初回アクセス時に SPW のログインパスワードを設定してください。
以降のパスワードデータは `data/spw.zip` に AES-256 で暗号化して保存されます。

## 更新（アップデート）

インストール後に更新する場合も、インストーラーを再実行します。

```bash
sudo bash /opt/lxd-data/spw/install-spw.sh
```

または Git リポジトリを直接更新します。

```bash
cd /opt/lxd-data/spw
git pull
sudo systemctl restart spw
```

※ `install-spw.sh` を再実行すると、ローカルで変更したコード（コミットしていない変更）は `origin/main` に上書きされます。

## データのバックアップ

パスワードデータ `data/` を丸ごとコピーしてください。

```bash
sudo tar czf /tmp/spw-backup.tar.gz -C /opt/lxd-data/spw data
```

復元する場合は同じ場所に展開してください。

```bash
sudo systemctl stop spw
sudo tar xzf /tmp/spw-backup.tar.gz -C /opt/lxd-data/spw
sudo systemctl start spw
```

## アンインストール

```bash
# 1. サービスを停止・無効化
sudo systemctl stop spw
sudo systemctl disable spw

# 2. データをバックアップ（残す場合。削除してよい場合はスキップ）
sudo tar czf /tmp/spw-backup.tar.gz -C /opt/lxd-data/spw data

# 3. サービス定義とインストール先を削除
sudo rm /etc/systemd/system/spw.service
sudo systemctl daemon-reload
sudo rm -rf /opt/lxd-data/spw

# 4. Tailscale Serve を解除（設定していた場合のみ）
sudo tailscale serve off
```

## 再インストール

```bash
sudo bash install-spw.sh   # GitHub から再インストール
```

`data/` が残っていれば、以前のデータをそのまま引き継げます。

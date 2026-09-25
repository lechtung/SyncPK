import json
import os

locales_dir = r'c:\Kodi_21_DSPlayer\_SyncPK\server\static\locales'

translations = {
    'de': {
        'install_lang_prompt': 'Wählen Sie Ihre bevorzugte Sprache für die Installation:',
        'install_plex_url': 'Geben Sie Ihre Plex-Server-URL ein (z. B. http://192.168.1.100:32400):',
        'install_plex_pass': 'Haben Sie ein aktives Plex Pass-Abonnement?',
        'install_plex_auth': 'Plex-Authentifizierung erforderlich!\n\nAuf dem nächsten Bildschirm sehen Sie einen Link. Kopieren Sie ihn und öffnen Sie ihn in Ihrem Browser. Das Skript wartet auf Ihre Autorisierung.',
        'install_pwd': 'Erstellen Sie ein Master-Passwort für das Web-Dashboard:',
        'install_pwd_confirm': 'Bestätigen Sie Ihr Master-Passwort:',
        'install_pwd_error': 'Passwörter stimmen nicht überein. Bitte versuchen Sie es erneut.',
        'install_tmdb': 'Geben Sie Ihren TMDB-API-Schlüssel ein (Kostenlos auf themoviedb.org), um Poster zu laden:',
        'install_sync_lang': 'Wählen Sie Ihre bevorzugte Sprache für TMDB/Plex-Metadaten:',
        'install_dash_lang': 'Wählen Sie Ihre bevorzugte Sprache für die Dashboard-Benutzeroberfläche:',
        'install_debug': 'Möchten Sie eine ausführliche DEBUG-Protokollierung für das Backend aktivieren?'
    },
    'fr': {
        'install_lang_prompt': 'Choisissez votre langue préférée pour l\'installation :',
        'install_plex_url': 'Entrez l\'URL de votre serveur Plex (par ex. http://192.168.1.100:32400) :',
        'install_plex_pass': 'Avez-vous un abonnement Plex Pass actif ?',
        'install_plex_auth': 'Authentification Plex requise !\n\nSur l\'écran suivant, vous verrez un lien. Copiez-le et ouvrez-le dans votre navigateur. Le script attendra votre autorisation.',
        'install_pwd': 'Créez un mot de passe maître pour le tableau de bord Web :',
        'install_pwd_confirm': 'Confirmez votre mot de passe maître :',
        'install_pwd_error': 'Les mots de passe ne correspondent pas. Veuillez réessayer.',
        'install_tmdb': 'Entrez votre clé API TMDB (Gratuite sur themoviedb.org) pour charger les affiches :',
        'install_sync_lang': 'Choisissez votre langue préférée pour les métadonnées TMDB/Plex :',
        'install_dash_lang': 'Choisissez votre langue préférée pour l\'interface utilisateur du tableau de bord :',
        'install_debug': 'Voulez-vous activer la journalisation DEBUG étendue pour le backend ?'
    },
    'it': {
        'install_lang_prompt': 'Scegli la tua lingua preferita per l\'installazione:',
        'install_plex_url': 'Inserisci l\'URL del tuo server Plex (es. http://192.168.1.100:32400):',
        'install_plex_pass': 'Hai un abbonamento Plex Pass attivo?',
        'install_plex_auth': 'Autenticazione Plex richiesta!\n\nNella schermata successiva, vedrai un link. Copialo e aprilo nel tuo browser. Lo script attenderà la tua autorizzazione.',
        'install_pwd': 'Crea una password principale per la Dashboard Web:',
        'install_pwd_confirm': 'Conferma la tua password principale:',
        'install_pwd_error': 'Le password non corrispondono. Riprova.',
        'install_tmdb': 'Inserisci la tua chiave API TMDB (Gratuita su themoviedb.org) per caricare le locandine:',
        'install_sync_lang': 'Scegli la tua lingua preferita per i metadati TMDB/Plex:',
        'install_dash_lang': 'Scegli la tua lingua preferita per l\'interfaccia della Dashboard:',
        'install_debug': 'Vuoi abilitare la registrazione DEBUG estesa per il backend?'
    },
    'pt': {
        'install_lang_prompt': 'Escolha o seu idioma preferido para a instalação:',
        'install_plex_url': 'Digite o URL do seu servidor Plex (ex: http://192.168.1.100:32400):',
        'install_plex_pass': 'Você tem uma assinatura ativa do Plex Pass?',
        'install_plex_auth': 'Autenticação Plex necessária!\n\nNa próxima tela, você verá um link. Copie-o e abra-o no seu navegador. O script aguardará sua autorização.',
        'install_pwd': 'Crie uma senha mestre para o Painel de Controle Web:',
        'install_pwd_confirm': 'Confirme sua senha mestre:',
        'install_pwd_error': 'As senhas não coincidem. Tente novamente.',
        'install_tmdb': 'Digite sua chave de API TMDB (Gratuita em themoviedb.org) para carregar os pôsteres:',
        'install_sync_lang': 'Escolha o seu idioma preferido para os metadados do TMDB/Plex:',
        'install_dash_lang': 'Escolha o seu idioma preferido para a interface do Painel de Controle:',
        'install_debug': 'Você deseja ativar o registro de DEBUG extenso para o backend?'
    },
    'ja': {
        'install_lang_prompt': 'インストールのための希望する言語を選択してください:',
        'install_plex_url': 'PlexサーバーのURLを入力してください（例：http://192.168.1.100:32400）:',
        'install_plex_pass': '有効なPlex Passサブスクリプションをお持ちですか？',
        'install_plex_auth': 'Plex認証が必要です！\n\n次の画面にリンクが表示されます。コピーしてブラウザで開いてください。スクリプトは認証を待機します。',
        'install_pwd': 'Webダッシュボードのマスターパスワードを作成してください:',
        'install_pwd_confirm': 'マスターパスワードを確認してください:',
        'install_pwd_error': 'パスワードが一致しません。もう一度お試しください。',
        'install_tmdb': 'ポスターを読み込むためにTMDB APIキーを入力してください（themoviedb.orgで無料）:',
        'install_sync_lang': 'TMDB/Plexメタデータの希望する言語を選択してください:',
        'install_dash_lang': 'ダッシュボードUIの希望する言語を選択してください:',
        'install_debug': 'バックエンドの詳細なDEBUGログを有効にしますか？'
    },
    'zh': {
        'install_lang_prompt': '请选择您首选的安装语言：',
        'install_plex_url': '请输入您的 Plex 服务器 URL（例如 http://192.168.1.100:32400）：',
        'install_plex_pass': '您有有效的 Plex Pass 订阅吗？',
        'install_plex_auth': '需要 Plex 身份验证！\n\n在下一个屏幕上，您将看到一个链接。复制它并在浏览器中打开。脚本将等待您的授权。',
        'install_pwd': '为 Web 仪表板创建主密码：',
        'install_pwd_confirm': '确认您的主密码：',
        'install_pwd_error': '密码不匹配。请重试。',
        'install_tmdb': '输入您的 TMDB API 密钥（在 themoviedb.org 上免费获得）以加载海报：',
        'install_sync_lang': '选择您首选的 TMDB/Plex 元数据语言：',
        'install_dash_lang': '选择您首选的仪表板界面语言：',
        'install_debug': '您是否要为后端启用广泛的 DEBUG 日志记录？'
    }
}

for lang, trans_dict in translations.items():
    file_path = os.path.join(locales_dir, f'{lang}.json')
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for k, v in trans_dict.items():
            if '\\n' in v:
                v = v.replace('\\n', '\\\\n')
            data[k] = v
        with open(file_path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f'Updated {lang}.json')

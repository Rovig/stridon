**[English](./README.md) | Français | [中文](./README-CN.md)**

# Stridon

Stridon traduit des livres, des documents et des articles web avec un modèle d'IA ou un service de traduction en ligne. Par défaut, vous obtenez le **document traduit**, avec sa structure (chapitres, titres, images, notes) ; avec `--bilingual`, chaque paragraphe original est suivi de sa traduction.

- **Formats :** EPUB, PDF (avec les images), TXT, Markdown, sous-titres SRT, articles web (en PDF et/ou EPUB)
- **Services :** Ollama (local, gratuit), OpenAI, Azure OpenAI, Claude, Gemini, DeepSeek, Groq, Mistral, OpenRouter, xAI, Qwen-MT, Google Traduction, DeepL, Caiyun, Tencent TranSmart, votre propre API
- **Toutes les langues et écritures**, y compris le chinois, le japonais, l'arabe et l'hébreu dans les PDF
- **Reprise automatique :** vous pouvez arrêter à tout moment ; relancez la même commande et la traduction reprend là où elle s'était arrêtée

À utiliser uniquement pour des textes que vous avez le droit de traduire (vos documents, des œuvres du domaine public, des articles pour votre lecture personnelle).

## Installation

Python 3.10 ou plus récent.

Le plus simple est [pipx](https://pipx.pypa.io), qui rend la commande `stridon` disponible partout :

```shell
git clone git@github.com:Rovig/stridon.git
pipx install ./stridon
```

Si le terminal répond ensuite « command not found: stridon », lancez `pipx ensurepath` et ouvrez un nouveau terminal. Pour mettre à jour après un `git pull` : `pipx install --force ./stridon` (ou installez une fois pour toutes avec `pipx install -e ./stridon`, et les modifications du code sont prises en compte immédiatement).

Sans pipx, installez-le dans un environnement virtuel ; la commande n'est alors disponible que lorsque l'environnement est activé :

```shell
cd stridon
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

Options facultatives : `pip install ".[tokens]"` pour un comptage exact des tokens avec `--accumulated-tokens`, et `pip install newspaper3k readability-lxml` pour plus de méthodes d'extraction des articles web.

## Démarrage rapide

```shell
# EPUB en français avec un modèle local (Ollama, service par défaut)
stridon livre.epub -l fr

# PDF en chinois avec Claude
stridon rapport.pdf -l zh -s claude

# Article web en PDF et EPUB avec Google Traduction
stridon https://exemple.com/article -l fr -s google -f both

# Essai sur les 10 premiers passages seulement
stridon livre.epub -l fr --test
```

Le résultat est écrit à côté du fichier d'origine sous le nom `<nom>_<langue>.<ext>`, par exemple `livre_fr.epub`, ou `<nom>_bilingual.<ext>` avec `--bilingual` (articles web : dans le dossier courant, nommés d'après leur titre). Utilisez `-o` pour choisir un autre fichier. Passer d'une présentation à l'autre ne coûte aucune nouvelle requête : les traductions sauvegardées sont réutilisées.

Si la traduction s'arrête (erreur, coupure réseau, Ctrl+C), un résultat partiel `<nom>_<langue>_temp.<ext>` est écrit. **Relancez la même commande** pour continuer : les passages déjà traduits sont réutilisés et ne sont pas renvoyés au service. Utilisez `--fresh` pour repartir de zéro.

Pensez à mettre les adresses web entre guillemets si elles contiennent `?` ou `&` : `stridon "https://exemple.com/article?id=42&lang=en"`.

## Services de traduction

Choisissez avec `-s/--service` et, si besoin, `-m/--model`. Les clés se donnent avec `--api-key` ou dans une variable d'environnement. Plusieurs clés ou modèles séparés par des virgules sont utilisés à tour de rôle.

| Service | `-s` | Modèle par défaut | Clé (variable d'environnement) |
|---|---|---|---|
| Ollama (local) | `ollama` (par défaut) | `aya` | aucune ; serveur `http://localhost:11434` ou `--api-base` |
| OpenAI | `openai` | `gpt-5-mini` | `OPENAI_API_KEY` |
| Azure OpenAI | `azure` | votre déploiement | `AZURE_OPENAI_API_KEY`, plus `--api-base` et `--azure-deployment` |
| Anthropic Claude | `claude` | `claude-opus-5-5` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini` | `gemini-2.5-flash` | `GEMINI_API_KEY` ou `GOOGLE_API_KEY` |
| DeepSeek | `deepseek` | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| Groq | `groq` | `llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| Mistral | `mistral` | `mistral-small-latest` | `MISTRAL_API_KEY` |
| OpenRouter | `openrouter` | obligatoire (`-m`) | `OPENROUTER_API_KEY` |
| xAI | `xai` | `grok-3-mini` | `XAI_API_KEY` |
| Qwen-MT (Alibaba) | `qwen` | `qwen-mt-turbo` | `DASHSCOPE_API_KEY` |
| Google Traduction | `google` | — | aucune |
| DeepL (RapidAPI « DeepL Translator ») | `deepl` | — | `RAPIDAPI_KEY` |
| Caiyun | `caiyun` | — | `CAIYUN_TOKEN` |
| Tencent TranSmart | `tencent` | — | aucune |
| Votre propre API | `custom` | — | URL dans `--api-base` |

Chaque service lit aussi `STRIDON_<SERVICE>_KEY` (par exemple `STRIDON_CLAUDE_KEY`).

Remarques :
- **Claude :** si le modèle refuse un passage, la requête est automatiquement relancée sur le modèle de secours recommandé par Anthropic.
- **Gemini :** le blocage de sécurité est désactivé ; un passage bloqué fait passer au modèle suivant de `-m`. Utilisez `--interval` pour espacer les requêtes.
- **Qwen-MT :** modèle dédié à la traduction ; il ignore `--prompt` et utilise `--context` comme mémoire de traduction.
- **Caiyun** ne traduit que vers le chinois simplifié, l'anglais et le japonais ; **Tencent** vers le chinois simplifié et l'anglais.
- **API personnalisée :** Stridon envoie `POST {"text", "source_lang", "target_lang"}` et lit `{"data": "<traduction>"}`.
- `--temperature`, `--source-lang`, `--extra-body '{"cle": "valeur"}'` (ajouté à chaque requête des services compatibles OpenAI et d'Ollama) et `--proxy` sont disponibles pour tous les services où ils ont un sens.

### Vos propres fournisseurs

Tout serveur compatible OpenAI (LM Studio, vLLM, une passerelle d'entreprise…) peut être déclaré dans `~/.stridon/providers.json` ou `./stridon_providers.json` (le fichier local l'emporte) :

```json
{
  "providers": {
    "lmstudio": {
      "api_style": "openai",
      "base_url": "http://localhost:1234/v1",
      "default_models": ["qwen2.5-7b-instruct"],
      "env_key": "LMSTUDIO_KEY"
    }
  }
}
```

Puis `stridon livre.epub --provider lmstudio`. `api_style` vaut `openai`, `claude`, `gemini`, `qwen` ou `ollama`.

## Options

### Générales

| Option | Description |
|---|---|
| `-l, --language` | Langue cible, en code (`fr`, `zh-hant`, `pt`…) ou en nom anglais (`French`) ; par défaut `fr` |
| `-o, --output` | Fichier de sortie |
| `--bilingual` | Alterne chaque paragraphe original et sa traduction (par défaut : traduction seule) |
| `--retranslate DÉBUT [FIN]` | Retraduit les passages, de celui qui contient DÉBUT à celui qui contient FIN, puis reconstruit le fichier ; tout le reste vient des traductions sauvegardées (tous formats ; `--chapter` limite la recherche à un fichier de chapitre EPUB) |
| `--test [N]` | Ne traduit que les N premiers passages (10 par défaut) |
| `--workers N` | Envoie N requêtes en parallèle |
| `--fresh` | Ignore les traductions sauvegardées par les exécutions précédentes |
| `--verbose` | Affiche chaque passage et sa traduction |

### Prompts et contexte (services d'IA)

| Option | Description |
|---|---|
| `--prompt` | Prompt personnalisé : un texte contenant `{text}` (et éventuellement `{language}`, `{crlf}` pour un retour à la ligne), une chaîne JSON `{"system": "...", "user": "...{text}..."}`, ou un fichier `.txt`, `.json` ou PromptDown `.md`. Les variables d'environnement `STRIDON_USER_PROMPT` / `STRIDON_SYSTEM_PROMPT` fonctionnent aussi |
| `--context [N]` | Envoie les N derniers paragraphes traduits (3 par défaut) pour garder noms, ton et vocabulaire cohérents ; les chapitres sont alors traduits dans l'ordre |
| `--block-size N` | Envoie N paragraphes par requête |
| `--accumulated-tokens N` | Regroupe les paragraphes en requêtes d'au plus N tokens |
| `--sentence-mode` | EPUB : chaque phrase est suivie de sa traduction (sortie bilingue) |

Les prompts en 3 étapes (`<step3_refined_translation>`) et les modèles qui « réfléchissent » (`<think>`) sont gérés : seule la traduction finale est gardée.

### API Batch d'OpenAI (moitié prix)

```shell
stridon livre.epub -s openai --batch-submit   # envoie tous les passages, rend la main aussitôt
stridon livre.epub -s openai --batch-fetch    # sous 24 h : récupère les résultats et construit le livre
```

### EPUB

| Option | Description |
|---|---|
| `--tags` | Balises à traduire (par défaut `p,h1,h2,h3,h4,h5,h6,li,blockquote,dt,dd` ; les légendes de figures sont incluses sauf avec `--no-captions`) |
| `--exclude-tags` | Balises jamais traduites, comme les appels de notes et le code (par défaut `sup,code`) |
| `--bare-text` | Traduit aussi le texte qui n'est dans aucune de ces balises |
| `--only-files`, `--skip-files` | Ne traduit que certains fichiers de chapitre, ou les saute (ex. `--skip-files toc.xhtml`) |
| `--style`, `--color` | Avec `--bilingual` : apparence des traductions, ex. `--color "#1e90ff"` |
| `--extract` | Produit un EPUB avec la traduction seule (`<nom>_monolingual.epub`) à partir d'un EPUB bilingue fait par Stridon |
| `--kobo [DOSSIER]` | Choisit un livre sur une liseuse Kobo (son dossier) ou dans l'application Kobo. Seuls les livres sans DRM peuvent être traduits |

Les descriptions d'images (texte alternatif) sont traduites aussi, sauf avec `--no-captions` ou `--no-images`.

### PDF

| Option | Description |
|---|---|
| `--pdf-mode enhanced` | Par défaut : un nouveau PDF avec les images à leur place. La traduction s'enchaîne de page en page comme un livre remis en page (les titres restent plus grands, les numéros de page d'origine disparaissent) ; avec `--bilingual`, chaque page d'origine donne une page avec l'original en noir et la traduction en bleu |
| `--pdf-mode simple` | Texte seul, écrit dans un `.txt` et un `.epub` |
| `--no-images` | Laisse les images de côté |
| `--min-image-size LxH` | Ignore les images plus petites (par défaut `50x50`) |
| `--image-quality 1-100` | Recompresse les images JPEG pour réduire la taille (EPUB aussi) ; 100 les laisse intactes |
| `--batch-lines N` | Texte sans lignes vides : nombre de lignes par passage (10 par défaut ; TXT aussi) |

### Articles web

| Option | Description |
|---|---|
| `-f, --format` | `pdf` (par défaut), `epub` ou `both` (les deux) |
| `--user-agent` | Identité de navigateur utilisée pour télécharger la page |

### Autres formats

- **TXT :** les paragraphes sont les blocs séparés par des lignes vides (un fichier sans ligne vide est découpé en groupes de `--batch-lines` lignes).
- **Markdown :** les blocs de code et les images restent tels quels ; titres et paragraphes sont traduits.
- **SRT :** numéros et horaires sont conservés ; les sous-titres affichent la traduction (avec `--bilingual`, l'original puis la traduction).

## Docker

```shell
docker build -t stridon .
docker run --rm -v "$PWD:/work" -e ANTHROPIC_API_KEY stridon livre.epub -l fr -s claude
```

Pour utiliser Ollama qui tourne sur la machine hôte : `--api-base http://host.docker.internal:11434`.

## Développement

```shell
pip install -e ".[dev]"
pytest
```

Organisation du code, et comment ajouter un service ou un format : [docs/architecture.md](docs/architecture.md) (en anglais).

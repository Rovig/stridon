**[English](./README.md) | 中文**

# Stridon

Stridon 将图书、文档和网页文章制作成**双语版本**：每个段落后面紧跟其译文，译文由 AI 模型或在线翻译服务生成。

- **格式：** EPUB、PDF（保留图片）、TXT、Markdown、SRT 字幕、网页文章（输出为 PDF 和/或 EPUB）
- **服务：** Ollama（本地、免费）、OpenAI、Azure OpenAI、Claude、Gemini、DeepSeek、Groq、Mistral、OpenRouter、xAI、通义千问 Qwen-MT、Google 翻译、DeepL、彩云小译、腾讯交互翻译，以及自定义 API
- **支持所有语言和文字**，PDF 中的中文、日文、阿拉伯文、希伯来文也能正确显示
- **自动续传：** 随时中断，再次运行同一条命令即可从中断处继续

请仅翻译您有权翻译的内容（自己的文档、公共领域作品、个人阅读的文章）。

## 安装

需要 Python 3.10 或更高版本。

```shell
git clone git@github.com:Rovig/stridon.git
cd stridon
pip install .
```

可选：`pip install ".[tokens]"`（`--accumulated-tokens` 精确计算 token），`pip install newspaper3k readability-lxml`（更多网页正文提取方式）。

## 快速开始

```shell
# 用本地模型（Ollama，默认服务）把 EPUB 翻译成中文
stridon book.epub -l zh

# 用 Claude 翻译 PDF
stridon report.pdf -l zh -s claude

# 用 Google 翻译把网页文章制作成 PDF 和 EPUB
stridon https://example.com/article -l zh -s google -f both

# 只翻译前 10 段试试效果
stridon book.epub -l zh --test
```

结果保存在原文件旁边，名为 `<文件名>_bilingual.<扩展名>`（网页文章保存在当前目录，以标题命名）。可用 `-o` 指定输出文件。

如果运行中断（出错、断网、Ctrl+C），会生成部分结果 `<文件名>_bilingual_temp.<扩展名>`。**再次运行同一条命令**即可继续：已翻译的段落会被复用，不会再次发送给服务。使用 `--fresh` 可从头开始。

## 翻译服务

用 `-s/--service` 选择服务，可用 `-m/--model` 指定模型。密钥通过 `--api-key` 或环境变量提供。多个以逗号分隔的密钥或模型会轮流使用。

| 服务 | `-s` | 默认模型 | 密钥（环境变量） |
|---|---|---|---|
| Ollama（本地） | `ollama`（默认） | `aya` | 无；服务器 `http://localhost:11434` 或 `--api-base` |
| OpenAI | `openai` | `gpt-5-mini` | `OPENAI_API_KEY` |
| Azure OpenAI | `azure` | 您的部署 | `AZURE_OPENAI_API_KEY`，以及 `--api-base` 和 `--azure-deployment` |
| Anthropic Claude | `claude` | `claude-opus-5-5` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini` | `gemini-2.5-flash` | `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` |
| DeepSeek | `deepseek` | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| Groq | `groq` | `llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| Mistral | `mistral` | `mistral-small-latest` | `MISTRAL_API_KEY` |
| OpenRouter | `openrouter` | 必须用 `-m` 指定 | `OPENROUTER_API_KEY` |
| xAI | `xai` | `grok-3-mini` | `XAI_API_KEY` |
| 通义千问 Qwen-MT | `qwen` | `qwen-mt-turbo` | `DASHSCOPE_API_KEY` |
| Google 翻译 | `google` | — | 无 |
| DeepL（RapidAPI "DeepL Translator"） | `deepl` | — | `RAPIDAPI_KEY` |
| 彩云小译 | `caiyun` | — | `CAIYUN_TOKEN` |
| 腾讯交互翻译 | `tencent` | — | 无 |
| 自定义 API | `custom` | — | 在 `--api-base` 中填写 URL |

每个服务也会读取 `STRIDON_<服务名>_KEY`（例如 `STRIDON_CLAUDE_KEY`）。

说明：
- **Claude：** 如果模型拒绝翻译某段内容，请求会自动交由 Anthropic 推荐的备用模型重新处理。
- **Gemini：** 已关闭安全拦截；某段内容被拦截时会切换到 `-m` 中的下一个模型。可用 `--interval` 放慢请求速度。
- **Qwen-MT：** 专用翻译模型，不使用 `--prompt`，`--context` 会作为翻译记忆发送。
- **自定义 API：** Stridon 发送 `POST {"text", "source_lang", "target_lang"}`，读取 `{"data": "<译文>"}`。

### 自定义服务商

任何兼容 OpenAI 的服务器（LM Studio、vLLM、公司网关等）都可以写入 `~/.stridon/providers.json` 或 `./stridon_providers.json`（本地文件优先）：

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

然后运行 `stridon book.epub --provider lmstudio`。`api_style` 可取 `openai`、`claude`、`gemini`、`qwen`、`ollama`。

## 选项

### 通用

| 选项 | 说明 |
|---|---|
| `-l, --language` | 目标语言，代码（`zh`、`zh-hant`、`ja`……）或英文名称（`Japanese`）；默认 `fr` |
| `-o, --output` | 输出文件 |
| `--only-translation` | 只保留译文，不保留原文 |
| `--test [N]` | 只翻译前 N 段（默认 10） |
| `--workers N` | 同时发送 N 个请求 |
| `--fresh` | 忽略之前保存的译文 |
| `--verbose` | 打印每段原文及译文 |

### 提示词与上下文（AI 服务）

| 选项 | 说明 |
|---|---|
| `--prompt` | 自定义提示词：包含 `{text}` 的文本（可选 `{language}`、表示换行的 `{crlf}`），JSON 字符串 `{"system": "...", "user": "...{text}..."}`，或 `.txt`、`.json`、PromptDown `.md` 文件。也可使用环境变量 `STRIDON_USER_PROMPT` / `STRIDON_SYSTEM_PROMPT` |
| `--context [N]` | 发送最近 N 段已翻译的段落（默认 3），使人名、语气和用词保持一致；此时各章节按顺序翻译 |
| `--block-size N` | 每个请求发送 N 段 |
| `--accumulated-tokens N` | 将段落合并为最多 N 个 token 的请求 |
| `--sentence-mode` | EPUB：每句后面紧跟其译文 |

### OpenAI 批量 API（半价）

```shell
stridon book.epub -s openai --batch-submit   # 一次性提交所有段落
stridon book.epub -s openai --batch-fetch    # 24 小时内：取回结果并生成图书
```

### EPUB

| 选项 | 说明 |
|---|---|
| `--tags` | 需要翻译的标签（默认 `p,h1,h2,h3,h4,h5,h6,li,blockquote,dt,dd`；图片说明也会翻译，除非使用 `--no-captions`） |
| `--exclude-tags` | 不翻译的标签，如脚注标记和代码（默认 `sup,code`） |
| `--bare-text` | 同时翻译不在上述标签内的文字 |
| `--only-files`、`--skip-files` | 只翻译或跳过某些章节文件 |
| `--style`、`--color` | 译文样式，例如 `--color "#1e90ff"` |
| `--retranslate START [END]` | 在 Stridon 生成的双语 EPUB 中，重新翻译从包含 START 的段落到包含 END 的段落（用 `--chapter` 指定章节文件） |
| `--extract` | 从 Stridon 生成的双语 EPUB 中提取仅含译文的 EPUB（`<文件名>_monolingual.epub`） |
| `--kobo [目录]` | 从 Kobo 阅读器（其目录）或 Kobo 桌面应用中选择图书。仅支持无 DRM 的图书 |

### PDF

| 选项 | 说明 |
|---|---|
| `--pdf-mode enhanced` | 默认：逐页生成新的双语 PDF，图片保留在原位 |
| `--pdf-mode simple` | 仅文字，输出 `<文件名>_bilingual.txt` 和 `.epub` |
| `--no-images` | 不保留图片 |
| `--min-image-size WxH` | 忽略小于该尺寸的图片（默认 `50x50`） |
| `--image-quality 1-100` | 重新压缩 JPEG 图片以减小体积（EPUB 同样适用）；100 表示不改动 |
| `--batch-lines N` | 没有空行的文本：每段的行数（默认 10，TXT 同样适用） |

### 网页文章

| 选项 | 说明 |
|---|---|
| `-f, --format` | `pdf`（默认）、`epub` 或 `both` |
| `--user-agent` | 下载网页时使用的浏览器标识 |

### 其他格式

- **TXT：** 以空行分隔的文字块为段落（没有空行的文件按 `--batch-lines` 行分组）。
- **Markdown：** 代码块和图片保持原样，标题和段落会被翻译。
- **SRT：** 保留序号和时间轴，每条字幕先显示原文再显示译文。

## Docker

```shell
docker build -t stridon .
docker run --rm -v "$PWD:/work" -e ANTHROPIC_API_KEY stridon book.epub -l zh -s claude
```

使用宿主机上的 Ollama：`--api-base http://host.docker.internal:11434`。

## 开发

```shell
pip install -e ".[dev]"
pytest
```

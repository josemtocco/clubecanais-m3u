# ClubeCanais → M3U para SS IPTV

Projeto para gerar, na raiz do repositório, uma playlist `clubecanais.m3u` a partir das páginas públicas do [ClubeCanais](https://clubecanais.com.br/).

## O que faz

- descobre canais da página inicial e das páginas de categorias;
- usa o nome apresentado pelo site;
- preserva a categoria informada pelo ClubeCanais em `group-title`;
- tenta obter logo quando a página disponibiliza uma imagem;
- extrai o stream público da página do canal;
- testa cada stream sem baixar a mídia inteira;
- remove canais cujo stream não responde na atualização;
- adiciona canais novos automaticamente;
- ordena a playlist por categoria e nome;
- gera `clubecanais.m3u` na raiz, compatível com importação no SS IPTV;
- gera `canais.json` e `cxtv-discovery.json` para diagnóstico;
- executa automaticamente a cada 6 horas pelo GitHub Actions;
- se a fonte sofrer uma falha grande, não substitui a última playlist válida.

## Arquivos

```text
/
├── .github/workflows/atualizar.yml
├── gerar_m3u.py
├── requirements.txt
├── .gitignore
├── README.md
├── clubecanais.m3u       # gerado pelo workflow
├── canais.json           # gerado pelo workflow
└── cxtv-discovery.json   # diagnóstico gerado pelo workflow
```

O workflow do GitHub precisa ficar em `.github/workflows`; os arquivos gerados pelo projeto ficam na raiz.

## Publicação

1. Crie um repositório no GitHub.
2. Envie os arquivos deste projeto.
3. Abra **Actions** e execute **Atualizar playlist ClubeCanais** manualmente uma vez.
4. Depois disso, o workflow roda a cada 6 horas.
5. No SS IPTV, use a URL `https://raw.githubusercontent.com/SEU_USUARIO/SEU_REPOSITORIO/main/clubecanais.m3u`.

## Segurança da atualização

O gerador só grava uma nova playlist quando encontra pelo menos 5 streams válidos (`MIN_VALID_CHANNELS=5`). Assim, uma indisponibilidade temporária do site não transforma a playlist em vazia.

O scraper não tenta contornar login, CAPTCHA, paywall ou mecanismos de proteção. Ele trabalha com informações e streams que a página pública disponibiliza ao navegador.

## Observação sobre streams

A disponibilidade de um stream é variável. Um HTTP 200/206 comprova apenas que o endereço respondeu ao teste naquele momento; não garante que o vídeo permanecerá disponível depois.

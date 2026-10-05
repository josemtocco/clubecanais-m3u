# ClubeCanais → M3U para SS IPTV

Projeto GitHub que percorre **cada categoria da barra lateral do ClubeCanais**, abre a página individual da categoria, executa o carregamento adicional quando existir e visita cada canal para descobrir o stream público usado pelo player.

## Estratégia
1. Lê as categorias diretamente da barra lateral de `index.php`.
2. Abre cada `index.php?category=...` por HTTP.
3. Abre cada categoria também no Chromium e clica em **Mostrar mais** enquanto novos canais aparecerem.
4. Une os canais por ID, preservando a categoria de origem.
5. Abre cada `channel.php?id=...` no Chromium.
6. Captura URLs de stream do DOM, JavaScript, `performance`, XHR/fetch e respostas do player.
7. Testa todos os candidatos encontrados para cada canal.
8. Mantém canais já conhecidos; acrescenta canais novos; só remove um canal conhecido diante de HTTP definitivo 404/410/451.
9. Gera `clubecanais.m3u`, `canais.json` e `cxtv-discovery.json` na raiz.

## Atualização
GitHub Actions executa automaticamente a cada 6 horas e também pode ser executado manualmente.

## SS IPTV
Use a URL Raw do arquivo `clubecanais.m3u` do repositório.

## Observação
O projeto coleta apenas o que o próprio ClubeCanais disponibiliza publicamente ao carregar as páginas e o player; não contorna login, CAPTCHA ou paywall.

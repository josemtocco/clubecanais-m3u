# ClubeCanais → M3U para SS IPTV

Gerador automático de playlist M3U a partir das páginas públicas do ClubeCanais.

## Correção desta versão

A versão anterior descobria muitos canais, mas descartava a maioria antes de montar a playlist porque o stream podia estar:

- criado somente depois que o player JavaScript era iniciado;
- em `video`, `source`, `iframe` ou `performance` do navegador;
- dentro de JSON/JavaScript;
- escapado como `https:\/\/...`;
- temporariamente bloqueado para o GitHub Actions por `401/403/429`, embora o endereço pudesse continuar utilizável no SS IPTV.

Esta versão separa descoberta, extração e validação. O navegador também captura requisições de rede e URLs de recursos carregados pelo player.

## Saídas na raiz

```text
clubecanais.m3u
canais.json
cxtv-discovery.json
```

A M3U usa `tvg-name`, `tvg-logo` e `group-title`, mantendo a categoria encontrada no ClubeCanais.

## Atualização

O GitHub Actions executa a cada 6 horas e também pode ser executado manualmente em **Actions**.

A playlist existente não é substituída se a coleta resultar em uma quantidade muito baixa de streams utilizáveis.

## Diagnóstico

`canais.json` e `cxtv-discovery.json` registram:

- total de páginas descobertas;
- canal e ID;
- categoria;
- URL do stream;
- status HTTP;
- resultado da validação.

`unknown_blocked` significa que o servidor respondeu com `401`, `403` ou `429`. Esse caso não é tratado automaticamente como canal morto, porque alguns servidores exigem cabeçalhos/contexto específicos.

## GitHub

A URL da playlist publicada será:

`https://raw.githubusercontent.com/SEU_USUARIO/SEU_REPOSITORIO/main/clubecanais.m3u`

O projeto não tenta contornar login, CAPTCHA, paywall ou mecanismos de proteção. Ele trabalha com os dados e streams que a página pública disponibiliza ao navegador.


### Fallback de segurança

O arquivo `canais-seed.json` guarda os oito canais que já haviam sido confirmados pela coleta anterior com HTTP 206. Se uma execução encontrar poucos streams por falha temporária do player ou do navegador, o gerador combina os canais encontrados com esses últimos canais conhecidos e ainda publica a playlist. No `canais.json` e em `cxtv-discovery.json`, esses registros aparecem com `validation: fallback_previous_active`, indicando que são dados previamente confirmados e não uma confirmação de disponibilidade em tempo real. Remova ou atualize esse arquivo quando quiser substituir a lista de fallback.

## Estratégia de atualização incremental (V5)

A playlist não é reconstruída exclusivamente a partir dos canais encontrados na execução atual.
O gerador:

1. descobre novamente todos os canais do ClubeCanais;
2. tenta extrair novos streams;
3. carrega os canais já conhecidos em `canais.json` e `canais-seed.json`;
4. revalida os streams já conhecidos;
5. mantém canais conhecidos quando o servidor responde normalmente ou quando há bloqueio/erro transitório;
6. remove um canal conhecido somente quando o stream retorna um erro HTTP definitivo (404, 410 ou 451);
7. acrescenta os novos canais encontrados;
8. grava a nova playlist sem perder os canais já válidos.

Assim, uma falha temporária na extração do player não reduz a playlist aos poucos canais encontrados naquela execução.


## Testes locais

Antes de publicar, execute `python -m unittest discover -s tests -v`. Os testes verificam deduplicação, metadados M3U e a presença dos oito canais de segurança. Eles não simulam nem garantem a disponibilidade dos streams externos.

## Limite importante

O projeto tenta descobrir os canais e extrair os streams acessíveis publicamente. Um canal listado no site não implica que exista uma URL direta de stream acessível ao GitHub Actions. Bloqueios, players proprietários e streams que exigem sessão podem impedir a extração. Os canais conhecidos são mesclados com os novos; falhas transitórias não devem zerar a playlist.

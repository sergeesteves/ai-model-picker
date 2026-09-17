---
name: quel-modele-ia
description: Comparateur « quel modèle IA choisir » qui croise qualité (Artificial Analysis, LMArena, Epoch AI), prix (OpenRouter) et usage réel (tokens traités sur OpenRouter), avec un classement déterministe et une formule affichée. Déclencheurs : « quel modèle pour du code / des agents », « meilleur rapport qualité/prix (chez OpenAI, Anthropic…) », « modèle le moins cher avec 1M de contexte », « quel LLM choisir », « compare les modèles IA », « quel modèle open source pour… ». Ne pas utiliser pour les modèles d'image/vidéo de Replicate (→ skills Replicate).
---

# Quel modèle IA choisir

Tu traduis la question en **une** commande, tu l'exécutes, tu restitues le tableau. Le classement est
calculé par le script, **jamais par toi** : ne réordonne pas, n'ajoute pas de modèle de mémoire, ne
corrige pas un score. Ton seul travail d'interprétation est le choix des options.

## Commande

```bash
python -m modelpicker recommend [options]
```

(Le paquet s'installe une fois avec `pip install -e <repo ai-model-picker>` ; sinon lancer la commande
depuis la racine du repo.) La première exécution du jour télécharge les sources (quelques secondes,
sortie de progression sur stderr) ; ensuite tout est lu dans le cache.

| Dans la question | Option |
|---|---|
| code, dev, programmation, refactor, front, webdev | `--task code` |
| agent, outils, tool calling, automatisation, workflow agentique | `--task agentic` (souvent avec `--tools`) |
| rédaction, chat, résumé, analyse, « en général », rien de précis | `--task general` (défaut) |
| « rapport qualité/prix », « meilleur pour le prix », rien de précis | `--sort value` (défaut) |
| « le meilleur », « le plus performant », budget indifférent | `--sort quality` |
| « le moins cher », « le plus économique » | `--sort price` + **toujours** un `--min-quality` (50 si « correct/décent » ou rien de précisé, 60 si « bon », 70 si « excellent ») |
| « le plus utilisé », « le plus adopté » | `--sort usage` |
| « chez OpenAI », « modèles Google » | `--author openai` (préfixe du slug OpenRouter : openai, anthropic, google, deepseek, z-ai, moonshotai, qwen, x-ai, mistralai, meta, minimax, tencent, xiaomi…) |
| « contexte d'un million » | `--min-context 1000000` |
| « moins de 1 $ le million » | `--max-price 1` (prix mixte) |
| open source, poids ouverts, auto-hébergeable | `--open-weights` |
| lit des images / PDF | `--input image` / `--input file` |
| « fiable », « valeur sûre » | `--min-sources 2` |
| « top 10 » | `--top 10` |

Si la question est ambiguë sur la tâche, prends `general` et dis-le en une phrase : ne pose pas de
question tant qu'une valeur par défaut raisonnable existe.

## Restitution

1. Une phrase de réponse directe : le n° 1 et pourquoi, **en citant les chiffres du tableau**
   (qualité, prix mixte, rang d'usage).
2. Le tableau tel que sorti par le script.
3. La formule et les sources telles que sorties (ne les résume pas : c'est la garantie de reproductibilité).
4. Si la section « Très utilisés mais sans score » apparaît, signale-la : ces modèles ne sont pas mauvais,
   ils ne sont pas évalués.
5. Les deux biais en fin de sortie restent affichés.

Pour réutiliser le résultat ailleurs (outil web, MCP, n8n), `--format json` renvoie la même chose en structuré.

## Diagnostic

- Données douteuses ou modèle attendu absent : `python -m modelpicker coverage` liste les modèles les
  plus utilisés sans score et les noms de benchmark non rapprochés ; corriger via `data/aliases.json`
  (jamais dans le code).
- Réseau indisponible : ajouter `--offline` pour utiliser le dernier cache.
- Méthode détaillée : `docs/METHODOLOGIE.md` du repo.

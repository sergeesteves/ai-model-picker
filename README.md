# ai-model-picker — quel modèle IA choisir ?

Comparateur **qualité × prix × usage réel** des modèles de langage, en une commande.

> « Meilleur rapport qualité/prix pour du code », « le meilleur chez OpenAI pour son prix »,
> « le moins cher avec un contexte d'un million et un bon niveau agentique ».

Ce qui le distingue : le croisement de trois signaux que les comparateurs séparent.

1. **Qualité** : benchmarks tiers fusionnés en percentiles (Artificial Analysis, LMArena, Epoch AI),
   avec le nombre de sources affiché à côté de chaque score.
2. **Prix** : tarifs OpenRouter, pondérés entrée/sortie selon la tâche.
3. **Adoption** : tokens réellement traités chaque jour sur OpenRouter.

Le classement est **déterministe et reproductible** : formule affichée, aucun LLM dans le calcul.
Méthode complète : [docs/METHODOLOGIE.md](docs/METHODOLOGIE.md).

## Installation

Python 3.10+, bibliothèque standard uniquement. `pyarrow` (optionnel) accélère la collecte LMArena.

```bash
git clone https://github.com/sergeesteves/ai-model-picker.git
cd ai-model-picker
pip install -e ".[fast]"
```

Aucune clé API, aucun secret. Le cache vit dans `~/.cache/ai-model-picker` (variable `MODEL_PICKER_CACHE` pour le déplacer).

## Utilisation

```bash
python -m modelpicker recommend --task code
python -m modelpicker recommend --author openai
python -m modelpicker recommend --task agentic --sort price --min-context 1000000 --min-quality 60
python -m modelpicker recommend --task code --open-weights --format json
```

| Option | Rôle |
|---|---|
| `--task general\|code\|agentic` | sources de qualité et pondération du prix |
| `--sort value\|quality\|price\|usage` | rapport qualité/prix (défaut), qualité, prix, adoption |
| `--author openai,anthropic` | éditeurs (préfixe du slug OpenRouter) |
| `--min-context`, `--max-price`, `--min-quality`, `--min-sources` | contraintes |
| `--open-weights`, `--input image`, `--tools` | poids ouverts, modalités d'entrée, appel d'outils |
| `--input-share`, `--price-weight`, `--adoption-weight` | paramètres de la formule |
| `--format md\|json`, `--top N`, `--offline` | sortie et cache |

Autres commandes : `collect` (télécharge les sources du jour, à planifier une fois par jour si besoin) et
`coverage` (diagnostic des rapprochements de noms entre sources, à corriger dans `data/aliases.json`).

Exemple de sortie (17/09/2026, `--task code`, extrait) :

| # | Modèle | Éditeur | Qualité (sources) | Prix mixte $/M | Usage/jour (rang) |
|---|---|---|---|---|---|
| 1 | DeepSeek V4 Flash 0731 ⓞ | deepseek | 65 (1/3) | 0,07 | 1 349 Md (#4) |
| 2 | GLM 5.3 Flash ⓞ | z-ai | 80 (3/3) | 0,11 | 1 799 Md (#2) |
| 3 | DeepSeek V4 Flash 0423 ⓞ | deepseek | 68 (3/3) | 0,10 | 564 Md (#8) |

## Skill Claude Code

`skills/quel-modele-ia/SKILL.md` traduit une question en langage naturel en options de la commande, puis
restitue le tableau sans le retoucher. Installation : copier (ou lier) le dossier dans `~/.claude/skills/`.

## Architecture

```
modelpicker/
  sources.py   collecte + cache quotidien (seul module qui touche le réseau)
  match.py     rapprochement des noms entre sources
  scoring.py   calcul pur : instantanés + requête -> dict JSON
  render.py    rendu Markdown
  cli.py       collect | recommend | coverage
data/          tasks.json (profils de tâche), aliases.json (rapprochements manuels)
```

`scoring.recommend()` est pur et renvoie du JSON : c'est le contrat commun au CLI, au skill, et aux
réutilisations prévues (outil web, serveur MCP). Voir [ROADMAP.md](ROADMAP.md).

## Limites

- L'usage vient d'un endpoint **non documenté** d'OpenRouter, qui peut changer sans préavis.
- Usage = développeurs via API : OpenAI, Anthropic et Google y sont sous-représentés.
- Les percentiles traduisent un rang, pas un écart de niveau.

## Données et licences

Les données appartiennent à leurs producteurs : [OpenRouter](https://openrouter.ai),
[Artificial Analysis](https://artificialanalysis.ai) (indices relayés par l'API OpenRouter),
[LMArena](https://lmarena.ai) ([dataset](https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset)),
[Epoch AI](https://epoch.ai/data) (CC-BY). Le dépôt ne redistribue aucune donnée : le cache reste local.
Vérifier les conditions de chaque source avant un usage commercial.

Code sous licence MIT, voir [LICENSE](LICENSE).

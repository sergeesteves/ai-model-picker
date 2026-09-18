# Méthodologie

Le classement est **déterministe** : mêmes instantanés + même requête = même tableau. Aucun LLM
n'intervient dans le calcul. Tous les paramètres sont affichés dans la sortie.

## 1. Sources

| Signal | Source | Accès | Fraîcheur | Couverture (sept. 2026) |
|---|---|---|---|---|
| Prix, contexte, modalités, poids ouverts | OpenRouter `GET /api/v1/models` | officiel, sans clé | quotidienne | ~440 modèles |
| Qualité : Intelligence / Coding / Agentic Index | Artificial Analysis, champ `benchmarks.artificial_analysis` de l'API OpenRouter | idem | quotidienne | ~187 modèles |
| Qualité : préférence humaine (Text, Coding, rédaction créative, respect des consignes, français, requêtes longues, WebDev, Agent) | LMArena, dataset Hugging Face `lmarena-ai/leaderboard-dataset`, split `latest` | données ouvertes | quelques jours | ~400 entrées par catégorie de `text` (281 en français), ~130 webdev, ~46 agent |
| Qualité : Epoch Capabilities Index | Epoch AI, `benchmark_data.zip` | données ouvertes (CC-BY) | ~2 semaines | ~270 modèles |
| Usage réel | OpenRouter `GET /api/frontend/v1/rankings/models?view=day` | **non documenté**, sans clé | J-1 | tous les modèles servis |
| Latence et vitesse | OpenRouter `GET /api/frontend/v1/stats/endpoint?permaslug=…&variant=standard&latencyMetric=latency&perfWorkload=text_generation` (onglet Performance d'une page modèle) | **non documenté**, sans clé, 1 appel par modèle (~320/jour, espacés de 0,3 s) | médianes des 30 dernières minutes au moment de la collecte | modèles servis avec trafic |

Pièges connus, gérés dans le code :

- Un modèle a plusieurs ids (`:batch`, `:free`, `:thinking`) et des alias glissants (`~openai/…-latest`) :
  seuls les ids sans `:` ni `~` sont classés, pour ne pas récupérer un tarif batch ou gratuit.
- L'usage est indexé par `model_permaslug` daté (`openai/gpt-5.6-luna-20260709`) : jointure sur
  `canonical_slug`, repli sur le slug sans suffixe de date.
- Le jour courant du classement d'usage est partiel : on prend toujours la dernière journée complète.
- Les vues week/month de l'endpoint d'usage ne sont pas des séries : l'historique est reconstitué par le
  cache local (moyenne sur les 7 derniers instantanés disponibles).
- L'éditeur est le préfixe du slug (`openai/`, `z-ai/`…). Les pages `/provider/…` d'OpenRouter comptent
  par hébergeur, pas par éditeur : non utilisées.
- LMArena publie les modèles par niveau d'effort (`claude-opus-5-max`, `…-high`) : le rapprochement retire
  ces suffixes et garde **le meilleur** niveau publié.
- Le endpoint `/filter` du dataset viewer Hugging Face renvoie des 500 intermittents et `/rows` limite à
  environ 50 pages : la collecte lit les fichiers parquet (1 requête par tableau) si `pyarrow` est installé,
  sinon `/rows` à rythme lent.

## 2. Rapprochement des noms

`normalize` : minuscules, préfixe éditeur retiré, séparateurs → `-`. Essais successifs : clé exacte, clé
sans jetons de variante (effort, budget `32k`, quantization, date, harnais), clé compacte sans tirets
(seulement si non ambiguë). Ce qui résiste se règle dans `data/aliases.json`.
`python -m modelpicker coverage` liste les meilleurs noms non rapprochés.

## 3. Formule

**Qualité Q (0-100).** Pour chaque source retenue par la tâche, on convertit le score du modèle en
**percentile** dans cette source (part des autres entrées moins bien notées, ex æquo pour moitié). Les
échelles hétérogènes (indice 0-100, Elo, ECI) deviennent ainsi comparables. Puis :

```
Q = (somme des percentiles des n sources disponibles + 50) ÷ (n + 1)
```

Le « + 50 » est un a priori neutre qui compte pour une source : un modèle noté par une seule source ne peut
pas dépasser 75, un modèle confirmé par trois sources peut atteindre 87,5. Le nombre de sources est affiché
à côté de chaque score.

| Tâche | Sources | Part d'entrée du prix mixte |
|---|---|---|
| `general` | AA Intelligence, LMArena Text, Epoch ECI | 80 % |
| `redaction` | AA Intelligence, LMArena rédaction créative, LMArena respect des consignes | 40 % |
| `redaction_fr` | LMArena français, LMArena rédaction créative, LMArena respect des consignes | 40 % |
| `code` | AA Coding, LMArena Coding, LMArena WebDev | 90 % |
| `agentic` | AA Agentic, LMArena Agent | 95 % |
| `documents` | AA Intelligence, LMArena requêtes longues, LMArena Text | 95 % |

Aucun benchmark public ne mesure la qualité SEO / GEO d'un texte : `redaction` juge l'écriture et le
respect des consignes, qui sont ce qui compte pour suivre un brief.

**D'où viennent ces parts d'entrée ? Ce sont des hypothèses, pas des mesures** : aucune source publique ne
donne la répartition entrée/sortie par type de tâche (l'API de classement d'OpenRouter accepte un paramètre de
catégorie mais l'ignore, testé le 18/09/2026 : chiffres identiques pour « marketing/seo », « programming »…).
Repères : 98 % des tokens traités sur OpenRouter sont de l'entrée (agents de code) ; un brief court qui produit un
article long est surtout de la sortie. La convention d'Artificial Analysis est un mélange 3:1 (75 % d'entrée).

Sensibilité mesurée le 18/09/2026 sur `redaction_fr` : à 40, 60, 80 et 95 % d'entrée, **les cinq finalistes
restent les mêmes**, seul leur ordre change (n° 1 DeepSeek V4 Flash 0423 à 40 %, GLM 5.3 Flash au-delà).

Exemples réels (ordres de grandeur, outils de l'auteur) : générateur de voix de marque ≈ 9 000 tokens lus pour
≈ 2 000 écrits (≈ 80 % d'entrée) ; question libre de ce comparateur ≈ 1 300 pour 120 (> 90 %). Un workflow de
rédaction qui injecte guide de style, exemples ou contenu des SERP est donc plus proche de l'entrée que de la
sortie. `--input-share` permet d'imposer son propre ratio.

Rédiger inverse l'économie des tokens : un brief court produit un long texte, donc le prix de **sortie** pèse
60 %. En `redaction_fr`, le classement des prompts en français remplace l'indice d'intelligence : un modèle
bon en anglais ne l'est pas toujours en français, et l'écart se voit (GLM 5.3 Flash est au 94ᵉ percentile
français, MiMo-V2.5 au 70ᵉ, pour une qualité de rédaction comparable).

Profils modifiables dans `data/tasks.json`. Repère : sur OpenRouter, 97 % des tokens traités le
16/09/2026 étaient des tokens d'entrée (trafic dominé par les agents de code).

**Prix mixte P ($ par million de tokens).** `P = s × prix d'entrée + (1 − s) × prix de sortie`, avec `s` la
part d'entrée de la tâche (`--input-share` pour la forcer). Plancher 0,05 $ dans la formule.

**Adoption A (0-100).** Percentile du modèle dans le classement d'usage OpenRouter (tokens/jour, moyenne
sur les instantanés disponibles) ; 0 s'il n'y figure pas.

**Score (tri `value`).**

```
score = Q × (1 + α × A / 100) ÷ max(P, 0,05)^β      α = 0,25   β = 0,5
```

- β = 0,5 : un prix doublé doit être compensé par +41 % de qualité. β = 1 favorise fortement les
  modèles très bon marché, β = 0 revient à trier par qualité.
- α = 0,25 : l'adoption départage (au plus +25 %) sans pouvoir faire gagner un modèle médiocre.
- Plancher de qualité par défaut à 60 pour ce tri (un « rapport qualité/prix » suppose une qualité correcte).

**Affichage : score qualité-prix sur 100.** Le score brut n'a pas d'unité (points de qualité par racine de
dollar) et ne se compare pas d'une recherche à l'autre. On affiche donc `score ÷ meilleur score × 100`, calculé
parmi tous les modèles qui passent les filtres : 100 = meilleur rapport qualité-prix de la liste. Même principe
pour le score rapide.

Autres tris : `quality` (Q décroissant, puis prix), `price` (P croissant, puis Q), `usage` (tokens/jour).

**Tri `fast` (rapide + qualité/prix)**, pour le chat et les usages temps réel :

```
score rapide = score × (0,5 + R / 100)
```

- Pour chaque hébergeur d'un modèle, OpenRouter publie la **latence** médiane avant le premier token (ms) et le
  **débit** médian (tokens/s) sur 30 minutes. On garde les hébergeurs en service (status 0) avec au moins
  10 requêtes, puis on fait la moyenne **pondérée par leurs requêtes** (ce que reçoit un appel « typique »
  routé par OpenRouter). Les instantanés quotidiens sont ensuite moyennés, jusqu'à 7 jours.
- R (réactivité, 0-100) = moyenne du percentile de débit (plus haut = mieux) et du percentile de latence
  (plus bas = mieux), parmi les modèles mesurés. Le modèle le plus réactif voit son score multiplié par 1,5,
  le moins réactif par 0,5.
- Filtres `max_latency_ms` et `min_throughput` ; un modèle sans mesure est exclu dès qu'un critère de vitesse
  est demandé (on n'invente pas une vitesse).
- Seuil de qualité par défaut à 60, comme pour `value`.
- Pourquoi pas la vitesse d'Artificial Analysis : elle couvre ~190 variantes sur 650, son délai avant la
  première réponse inclut la réflexion (150 s pour un modèle en effort maximal), et chaque niveau d'effort
  devrait être rapproché d'un modèle OpenRouter. OpenRouter mesure le trafic réel, sur ses propres identifiants.

Un modèle sans aucun score pour la tâche n'est jamais classé ; s'il figure dans le top 25 d'usage, il est
signalé à part.

## 4. Biais à assumer

- **Usage = développeurs via l'API OpenRouter**, pas le grand public. OpenAI, Anthropic et Google sont
  sous-représentés par rapport à leurs canaux directs ; les modèles économiques dominent mécaniquement le volume.
- **Percentile = rang, pas écart** : deux modèles à 1 point d'Elo peuvent être séparés de plusieurs percentiles.
- **Populations différentes** : les percentiles AA sont calculés sur les modèles d'OpenRouter, ceux de LMArena
  et d'Epoch sur leurs propres tableaux, qui incluent d'anciens modèles.
- **Niveau d'effort** : LMArena note souvent la version « max » ; le prix au token affiché ne dit rien du
  surcroît de tokens de réflexion.
- **Poids ouverts** = id Hugging Face déclaré sur OpenRouter (heuristique).
- **Usage gratuit compté** : les variantes `:free` représentent ~8 % des tokens (17/09/2026). Test empirique :
  les exclure ne change **aucun top 5** sur 18 classements (6 tâches × 3 tris), seulement des rangs 6 à 10 dans
  6 cas. On garde donc les chiffres d'OpenRouter tels quels.
- **Vitesse instantanée** : médianes sur 30 minutes, qui varient avec l'heure de collecte, la charge des
  hébergeurs et la longueur des réponses ; la moyenne sur plusieurs jours lisse en partie. Pour un modèle qui
  réfléchit, la latence avant le premier token inclut souvent la réflexion.

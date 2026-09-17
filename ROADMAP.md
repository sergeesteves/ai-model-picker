# Feuille de route

## v1 : skill Claude Code (fait)

- Collecte quotidienne en cache local : OpenRouter (prix, indices AA, usage), LMArena, Epoch AI.
- `recommend` : tâche, éditeur, contraintes, 4 tris, formule et sources affichées, sortie Markdown ou JSON.
- `coverage` : diagnostic des rapprochements.
- Skill `quel-modele-ia`.

## v1.x : fiabiliser

- [ ] Compléter `data/aliases.json` à partir de `coverage` (Qwen Max, Gemini Pro, Muse Spark…).
- [ ] Planifier `collect` chaque jour pour que la moyenne d'usage sur 7 jours soit pleine.
- [ ] Importer l'historique de la data table n8n `openrouter_daily` (depuis le 06/09/2026) pour amorcer le cache.
- [ ] Artificial Analysis en direct via leur API (clé gratuite, vérifier les conditions d'usage) : plus de
      benchmarks, et couverture des nouveautés absentes du relais OpenRouter. Clé en variable d'environnement,
      jamais dans le repo.
- [ ] SWE-bench / Terminal-Bench (Epoch) comme sources `code` et `agentic` supplémentaires.
- [ ] Intégration continue : tests unitaires sur chaque push.

## v2 : outil web sous `www.creapulse.fr/outils/quel-modele-ia` (code fait le 2026-09-17, déploiement à faire)

Décision (2026-09-17) : **page vitrine d'expertise, gratuite, non monétisée**.

- Micro-app (même patron que le générateur de voix de marque) qui appelle `scoring.recommend()`, cache
  rafraîchi par tâche planifiée côté serveur. Jamais de sous-domaine.
- Deux entrées :
  - **Formulaire** (tâche, tri, éditeur, contraintes) : déterministe, 0 appel LLM.
  - **Question libre** : un seul appel LLM, qui traduit la question en requête JSON (schéma = `scoring.DEFAULTS`).
    Le LLM ne classe rien et ne rédige pas la réponse : phrase de synthèse générée par gabarit à partir du résultat.
- Garde-fous de coût : petit modèle économique via OmniRoute avec une clé dédiée et un budget quotidien plafonné ;
  sortie validée par liste blanche (valeur hors liste = ignorée) ; cache des questions déjà vues ; limite par IP.
  Budget épuisé → la question libre est désactivée pour la journée, le formulaire continue de marcher.
- Ordre de grandeur : ~1 300 tokens d'entrée + ~120 de sortie par question ; avec un modèle à ~0,10 $ / 0,30 $
  le million, ≈ 0,00015 $ la question, soit ~6 500 questions pour 1 $.
- Charte visuelle et voix Creapulse (skills `creapulse-brand-design` / `creapulse-brand-voice`) pour l'interface
  et les textes.

## v3 : serveur MCP

- Outil `recommend_model` dont le schéma d'entrée reprend exactement `scoring.DEFAULTS`, sortie = JSON
  de `recommend()`.
- Outil `explain_model` : détail des sources d'un modèle.

## Hors périmètre, décidé

- Pas de graphe qualité × prix ni de section Design Arena sur la page « IA la plus utilisée » (WP 4045) :
  cet angle appartient au comparateur.
- Pas d'agent conversationnel en v1 : le LLM ne sert qu'à traduire la question en options.

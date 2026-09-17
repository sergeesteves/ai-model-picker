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

## v2 : outil web sous `www.creapulse.fr/outils/…`

- Micro-app (même patron que le générateur de voix de marque) qui appelle `scoring.recommend()`, cache
  rafraîchi par tâche planifiée côté serveur.
- Jamais de sous-domaine ; identité via WordPress + PMPro (jeton HMAC émis par creapulse-tools).
- Palier gratuit : questions prédéfinies ; palier membre : filtres complets, export JSON, alertes.
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

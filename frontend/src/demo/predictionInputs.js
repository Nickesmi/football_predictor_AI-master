// Whether the model's required inputs were available for a match (api/services/match_analysis_service.py
// returns them as `missing_inputs`). Since 2026-11-10 the identity path always predicts from the best level
// of the fallback chain (src/identity/fallback_chain.py) and labels its confidence; it is unavailable only
// when no stored result exists at any level. The legacy name-only path still blocks on "has NO real data"
// (it would otherwise run on hard-coded defaults).
const BLOCKING = /has NO real data|already includes results dated|at any level of the fallback chain/;
export const predictionAvailability = (analysis) => {
  const notes = analysis && Array.isArray(analysis.missing_inputs) ? analysis.missing_inputs : [];
  const blocking = notes.filter((n) => BLOCKING.test(n));
  const available = blocking.length === 0 && !(analysis && analysis.prediction_available === false);
  return { available, blocking, warnings: notes.filter((n) => !blocking.includes(n)) };
};

// The fixture's provider ids for the analysis request, so the backend resolves each team by identity
// (src/identity/analysis_inputs.py) instead of by name alone.
export const identityQuery = (fixture) => {
  if (!fixture || !fixture.provider) return '';
  const params = {
    provider: fixture.provider === 'football_data' ? 'football_data' : (fixture.source || fixture.provider),
    league_id: fixture.league_id_apifootball || fixture.league?.id,
    league_code: fixture.league?.code,
    country: fixture.league?.country,
    home_id: fixture.home_team?.id,
    away_id: fixture.away_team?.id,
    kickoff_utc: fixture.kickoff_utc,
  };
  return Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `&${k}=${encodeURIComponent(v)}`).join('');
};

// Data confidence (High / Medium / Low) and what each side's numbers are based on.
const LEVEL_TEXT = {
  team: 'own results', league: 'league averages', country: 'country averages',
  competition_type: 'competition-type averages', gender_age: 'gender and age-group averages',
  all_results: 'all stored results',
};
const levelText = (level) => {
  if (!level) return '';
  if (level.startsWith('team+')) return `own results + ${LEVEL_TEXT[level.slice(5)] || level.slice(5)}`;
  return LEVEL_TEXT[level] || level;
};
export const CONFIDENCE_STYLE = {
  High: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  Medium: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  Low: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
};
export const confidenceView = (analysis) => {
  const c = analysis && analysis.confidence;
  if (!c || !c.level) return null;
  const basis = (analysis && analysis.data_basis) || {};
  const sides = ['home', 'away'].filter((s) => basis[s]).map((s) => {
    const b = basis[s];
    return {
      side: s, team: b.team, ownMatches: b.own_matches,
      goals: levelText(b.goals && b.goals.level),
      corners: levelText(b.corners && b.corners.level),
      cards: levelText(b.cards && b.cards.level),
    };
  });
  return { level: c.level, note: c.note || '', style: CONFIDENCE_STYLE[c.level] || CONFIDENCE_STYLE.Low, sides,
           baseline: c.baseline || null, elo: c.elo || null, profile: analysis.league_profile || null };
};

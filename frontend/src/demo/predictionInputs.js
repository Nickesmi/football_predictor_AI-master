// Whether the model's required inputs were available for a match (api/services/match_analysis_service.py
// returns them as `missing_inputs`). A team with no real data, or whose only history record already contains
// results from on or after the kick-off (src/identity/analysis_inputs.py), gets no prediction.
const BLOCKING = /has NO real data|already includes results dated/;
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

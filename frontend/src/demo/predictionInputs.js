// Whether the model's required inputs were available for a match (api/services/match_analysis_service.py
// returns them as `missing_inputs`). A team with no real data means the probabilities would come only
// from fallback defaults: the prediction is shown as unavailable instead.
export const predictionAvailability = (analysis) => {
  const notes = analysis && Array.isArray(analysis.missing_inputs) ? analysis.missing_inputs : [];
  const blocking = notes.filter((n) => /has NO real data/.test(n));
  return { available: blocking.length === 0, blocking, warnings: notes.filter((n) => !blocking.includes(n)) };
};

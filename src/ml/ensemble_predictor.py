import numpy as np
from scipy.optimize import minimize
from sklearn.metrics import log_loss
from typing import Dict

class EnsemblePredictor:
    """
    Learns optimal weights for an ensemble of models by minimizing log loss
    on a validation set. Strictly bounds weights between [0, 1] and enforces sum(weights) == 1.
    """
    def __init__(self):
        self.weights = None
        self.model_names = []
        
    def fit_weights(self, models_val_preds: Dict[str, np.ndarray], y_val: np.ndarray):
        """
        models_val_preds: Dict where key is model name, value is array of shape (N, 3) probabilities
        y_val: array of shape (N,) true labels
        """
        self.model_names = list(models_val_preds.keys())
        n_models = len(self.model_names)
        
        if n_models == 0:
            raise ValueError("No model predictions provided.")
            
        preds_array = np.array([models_val_preds[name] for name in self.model_names]) # (M, N, 3)
        
        def objective(weights):
            blended = np.tensordot(weights, preds_array, axes=([0], [0]))
            # Small epsilon to avoid log(0) issues inside log_loss
            blended = np.clip(blended, 1e-15, 1 - 1e-15)
            # Re-normalize just in case
            blended = blended / np.sum(blended, axis=1, keepdims=True)
            return log_loss(y_val, blended, labels=[0, 1, 2])
            
        initial_weights = np.ones(n_models) / n_models
        bounds = [(0, 1) for _ in range(n_models)]
        constraints = {'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}
        
        result = minimize(objective, initial_weights, bounds=bounds, constraints=constraints, method='SLSQP')
        self.weights = result.x
        
    def predict_proba(self, models_test_preds: Dict[str, np.ndarray]) -> np.ndarray:
        if self.weights is None:
            raise ValueError("Ensemble weights not learned yet.")
            
        preds_array = np.array([models_test_preds[name] for name in self.model_names])
        blended = np.tensordot(self.weights, preds_array, axes=([0], [0]))
        return blended
        
    def get_weights_dict(self) -> Dict[str, float]:
        if self.weights is None:
            return {}
        return {name: float(self.weights[i]) for i, name in enumerate(self.model_names)}

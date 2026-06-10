from catboost import CatBoostClassifier
import numpy as np

class CatBoostPredictor:
    """
    CatBoost wrapper for predicting match outcomes (Home, Draw, Away).
    """
    def __init__(self):
        self.model = CatBoostClassifier(
            iterations=50,
            learning_rate=0.05,
            depth=6,
            loss_function='MultiClass',
            eval_metric='MultiClass',
            verbose=False,
            early_stopping_rounds=50
        )
        self.is_trained = False
        
    def train(self, X_train: np.ndarray, y_train: np.ndarray, X_val: np.ndarray = None, y_val: np.ndarray = None):
        eval_set = (X_val, y_val) if X_val is not None and y_val is not None else None
        self.model.fit(
            X_train, y_train,
            eval_set=eval_set,
            use_best_model=True if eval_set else False
        )
        self.is_trained = True
        
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self.is_trained:
            raise ValueError("CatBoost model is not trained yet.")
        return self.model.predict_proba(X)

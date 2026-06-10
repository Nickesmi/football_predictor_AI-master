import lightgbm as lgb
import numpy as np

class LightGBMPredictor:
    """
    LightGBM wrapper for predicting match outcomes (Home, Draw, Away).
    """
    def __init__(self):
        self.model = None
        self.params = {
            'objective': 'multiclass',
            'num_class': 3,
            'metric': 'multi_logloss',
            'boosting_type': 'gbdt',
            'learning_rate': 0.05,
            'max_depth': 6,
            'num_leaves': 31,
            'feature_fraction': 0.8,
            'verbose': -1
        }
        
    def train(self, X_train: np.ndarray, y_train: np.ndarray, X_val: np.ndarray = None, y_val: np.ndarray = None):
        train_data = lgb.Dataset(X_train, label=y_train)
        valid_sets = [train_data]
        if X_val is not None and y_val is not None:
            val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)
            valid_sets.append(val_data)
            
        self.model = lgb.train(
            self.params,
            train_data,
            num_boost_round=1000,
            valid_sets=valid_sets,
            callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)] if len(valid_sets) > 1 else []
        )
        
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise ValueError("LightGBM model is not trained yet.")
        return self.model.predict(X)

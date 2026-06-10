from tabpfn import TabPFNClassifier
import numpy as np

class TabPFNPredictor:
    """
    TabPFN wrapper for predicting match outcomes.
    Note: TabPFN is a pre-trained transformer. It stores the training set
    and uses it as context for inference. It scales O(N^2), so we aggressively
    subsample the training data to a maximum of 5,000 rows to prevent OOM.
    """
    def __init__(self):
        self.model = TabPFNClassifier(device='cpu')
        self.is_trained = False
        
    def train(self, X_train: np.ndarray, y_train: np.ndarray, X_val: np.ndarray = None, y_val: np.ndarray = None):
        max_rows = 500
        if len(X_train) > max_rows:
            # Subsample to avoid memory issues with TabPFN
            np.random.seed(42)
            indices = np.random.choice(len(X_train), max_rows, replace=False)
            X_train = X_train[indices]
            y_train = y_train[indices]
            
        self.model.fit(X_train, y_train)
        self.is_trained = True
        
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self.is_trained:
            raise ValueError("TabPFN model is not fitted yet.")
        # TabPFN predict_proba returns the probabilities.
        # Ensure they are normalized just in case.
        probs = self.model.predict_proba(X)
        probs = probs / np.sum(probs, axis=1, keepdims=True)
        return probs

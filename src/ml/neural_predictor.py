import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import TensorDataset, DataLoader
import numpy as np

class MLPModel(nn.Module):
    def __init__(self, input_dim: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 256),
            nn.BatchNorm1d(256),
            nn.ReLU(),
            nn.Dropout(0.3),
            
            nn.Linear(256, 128),
            nn.BatchNorm1d(128),
            nn.ReLU(),
            nn.Dropout(0.2),
            
            nn.Linear(128, 64),
            nn.ReLU(),
            
            nn.Linear(64, 3)
        )
        
    def forward(self, x):
        return self.net(x)

class NeuralPredictor:
    """
    PyTorch Multi-Layer Perceptron wrapper for predicting match outcomes.
    """
    def __init__(self):
        self.model = None
        
    def train(self, X_train: np.ndarray, y_train: np.ndarray, X_val: np.ndarray = None, y_val: np.ndarray = None):
        input_dim = X_train.shape[1]
        self.model = MLPModel(input_dim)
        
        train_dataset = TensorDataset(torch.FloatTensor(X_train), torch.LongTensor(y_train))
        train_loader = DataLoader(train_dataset, batch_size=64, shuffle=True)
        
        optimizer = optim.Adam(self.model.parameters(), lr=0.001)
        criterion = nn.CrossEntropyLoss()
        
        best_loss = float('inf')
        patience = 5
        patience_counter = 0
        
        for epoch in range(20):
            self.model.train()
            for batch_X, batch_y in train_loader:
                optimizer.zero_grad()
                outputs = self.model(batch_X)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()
                
            if X_val is not None and y_val is not None:
                self.model.eval()
                with torch.no_grad():
                    val_outputs = self.model(torch.FloatTensor(X_val))
                    val_loss = criterion(val_outputs, torch.LongTensor(y_val)).item()
                    
                if val_loss < best_loss:
                    best_loss = val_loss
                    patience_counter = 0
                    # Could save model state dict here for true early stopping restoration
                else:
                    patience_counter += 1
                    
                if patience_counter >= patience:
                    break
                    
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise ValueError("Neural model is not trained yet.")
            
        self.model.eval()
        with torch.no_grad():
            outputs = self.model(torch.FloatTensor(X))
            probs = torch.softmax(outputs, dim=1).numpy()
        return probs

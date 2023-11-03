import torch
import torch.nn as nn
import pickle
import numpy as np
import datetime
from net import MLP
from utils import *
from timeit import default_timer

n_train = 180000
n_test = 10000

class Simple1DCNN(nn.Module):
    def __init__(self):
        super(Simple1DCNN, self).__init__()

        self.features = nn.Sequential(
            # Add channel dimension: [batch_size, 3] -> [batch_size, 1, 3]
            # This step is moved to the forward method
            
            # Convolutional layers
            nn.Conv1d(in_channels=1, out_channels=16, kernel_size=2, stride=1, padding=0),
            nn.ReLU(),
            
            nn.Conv1d(in_channels=16, out_channels=32, kernel_size=2, stride=1, padding=0),
            nn.ReLU()
        )

        self.classifier = nn.Sequential(
            # Flatten and pass to a fully connected layer
            nn.Linear(32, 128),
            nn.ReLU(),
        )

    def forward(self, x):
        # Add channel dimension
        x = x.unsqueeze(1)
        x = self.features(x)

        # Flatten: [batch_size, channels, length] -> [batch_size, channels * length]
        x = x.view(x.size(0), -1)
        
        x = self.classifier(x)
        return x
    
class DeepONet(nn.Module):
    def __init__(self, branch, trunk_layers):
        super(DeepONet, self).__init__()
        if isinstance(branch, list):
            self.branch_net = MLP(branch, type='relu', last_activation=False)
        else:
            self.branch_net = branch
        self.trunk_net = MLP(trunk_layers, type='relu', last_activation=True)
        self.bias = nn.Parameter(torch.zeros(1))  

    def forward(self, branch, trunk):
        branch_output = self.branch_net(branch)
        trunk_output = self.trunk_net(trunk)

        output = torch.einsum("bi,bji->bj", branch_output, trunk_output) + self.bias
        return output
    
    def run(self, x_train, y_train, x_test, y_test, batch_size, device, criterion='mse', iterations=50, model=None):
        if criterion == 'mse':
            criterion = nn.MSELoss()
        else:
            criterion = LpLoss(size_average=False)
            
        # x_train[0] for branch, x_train[1] for trunk
        data_train = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(x_train[0].to(device), x_train[1].to(device), y_train.to(device)), batch_size=batch_size, shuffle=True)
        data_test = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(x_test[0].to(device), x_test[1].to(device), y_test.to(device)), batch_size=batch_size, shuffle=False)

        if model:
            self.model = model.to(device)
        else:
            self.model = self.to(device)
        self.best_model = (self.model, 1e10) # (model, loss)
        
        optimizer = torch.optim.Adam(self.model.parameters(), lr=1e-3, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=iterations / 10, gamma=0.5)
        myloss = LpLoss(size_average=False)

        for it in range(iterations):
            # self.model.train()
            t1 = default_timer()
            train_l2 = 0
            train_mse = 0
            for branch, trunk, y in data_train:
                optimizer.zero_grad()
                out = self.model(branch, trunk)
                
                mse = criterion(out.view(batch_size, -1), y.view(batch_size, -1))
                mse.backward()
                
                # out = y_normalizer.decode(out)
                # y = y_normalizer.decode(y)
                loss = myloss(out.view(batch_size,-1), y.view(batch_size,-1))
                # loss.backward()
        
                optimizer.step()
                train_mse += mse.item()
                train_l2 += loss.item()
        
            scheduler.step()
        
            # self.model.eval()
            test_l2 = 0.0
            with torch.no_grad():
                for branch_x, trunk_x, y in data_test:
                    out = self.model(branch_x, trunk_x)
                    # out = y_normalizer.decode(out)
                    test_l2 += myloss(out.view(batch_size,-1), y.view(batch_size,-1)).item()
        
            train_mse /= n_train
            train_l2/= n_train
            test_l2 /= n_test
        
            t2 = default_timer()
            
            if test_l2 < self.best_model[1]:
                self.best_model = (self.model, test_l2)
                
            print("Epoch: %d, time: %.3f, Train Loss: %.3e, Train l2: %.4f, Test l2: %.4f" 
                    % ( it, t2-t1, train_mse, train_l2, test_l2) )
            error = torch.sqrt(torch.mean(((self.model(x_test[0].to(device), x_test[1].to(device)) - y_test.to(device)) / torch.sqrt(y_test.to(device))) ** 2))
            print(f'error={error}')
        return self.model
    
    def predict(self, x_test, device):
        current_time = datetime.datetime.now()
        formatted_time = current_time.strftime('%Y-%m-%d_%H-%M-%S')

        y_pred = self.best_model[0](x_test[0].to(device), x_test[1].to(device))
        np.savetxt(f'./log/{formatted_time}.txt', y_pred.cpu().detach().numpy())

with open('data_train.pkl', 'rb') as f:
    x_train, y_train, z_train, PE_total_train, Ek_train, Evis_train = pickle.load(f)
    x_normalizer = Normalizer(x_train)
    y_normalizer = Normalizer(y_train)
    z_normalizer = Normalizer(z_train)
    PE_total_train = PE_total_train.astype('float64')
    PE_normalizer = Normalizer(PE_total_train)
    
    x_train = x_normalizer.encode(torch.from_numpy(x_train[:, np.newaxis]).float())
    y_train = y_normalizer.encode(torch.from_numpy(y_train[:, np.newaxis]).float())
    z_train = z_normalizer.encode(torch.from_numpy(z_train[:, np.newaxis]).float())
    PE_total_train = PE_normalizer.encode(torch.from_numpy(PE_total_train[:, np.newaxis, np.newaxis]).float())
    
with open('data_test.pkl', 'rb') as f:
    x_test, y_test, z_test, PE_total_test, Ek_test, Evis_test = pickle.load(f)
    PE_total_test = PE_total_test.astype('float64')
    x_test = x_normalizer.encode(torch.from_numpy(x_test[:, np.newaxis]).float())
    y_test = y_normalizer.encode(torch.from_numpy(y_test[:, np.newaxis]).float())
    z_test = z_normalizer.encode(torch.from_numpy(z_test[:, np.newaxis]).float())
    PE_total_test = PE_normalizer.encode(torch.from_numpy(PE_total_test[:, np.newaxis, np.newaxis]).float())

x_train = (torch.cat([x_train, y_train, z_train], dim=1), PE_total_train)
x_test = (torch.cat([x_test, y_test, z_test], dim=1), PE_total_test)
y_train = torch.from_numpy(Evis_train[:, np.newaxis])
y_test = torch.from_numpy(Evis_test[:, np.newaxis])

model = DeepONet(branch=[3, 128, 128, 128],
                 trunk_layers=[1, 128, 128, 128])
# model = DeepONet(branch=Simple1DCNN(),
#                  trunk_layers=[1, 128, 128, 128])

device = 'cuda:6' if torch.cuda.is_available() else (torch.device('mps') if torch.backends.mps.is_available() else 'cpu')

model.run(x_train, y_train, x_test, y_test, batch_size=500, device=device, criterion='l2')
model.predict(x_test, device=device)
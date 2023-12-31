import torch
import torch.nn as nn
import torch.nn.init as init
import torch.nn.functional as F
import torchvision.models as models
import datetime
import numpy as np
from utils import *
from timeit import default_timer

class MLP(torch.nn.Module):
    
    def __init__(self, layer_sizes, type='tanh', last_activation=False):
        super(MLP, self).__init__()
        layers = []
        for i in range(len(layer_sizes) - 1):
            layers.append(nn.Linear(layer_sizes[i], layer_sizes[i + 1]))
            # layers.append(torch.nn.Linear(layer_sizes[i], layer_sizes[i + 1]))
            if i < (len(layer_sizes) - 2 if not last_activation else len(layer_sizes) - 1):  # add an activation function when not in the last layer
                if type=='tanh':
                    layers.append(nn.Tanh())
                if type=='relu':
                    layers.append(nn.ReLU())
                if type=='gelu':
                    layers.append(nn.GELU())
                # layers.append(nn.ReLU())
        self.linears = nn.Sequential(*layers)
        self.initialize_weights()

    def initialize_weights(self):
        for module in self.modules():
            if isinstance(module, nn.Linear):
                # use xavier_uniform_ initialization
                # init.xavier_uniform_(module.weight)
                # or，use xavier_normal_ initialization
                init.xavier_normal_(module.weight)
                # set all biases to 0
                init.zeros_(module.bias)
                
    def forward(self, x):
        return self.linears(x)
    
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

class ResNet(nn.Module):
    def __init__(self, out_features=3):
        super(ResNet, self).__init__()
        self.resnet = models.resnet18()

        # 读取原始全连接层的输入特征数量
        num_features = self.resnet.fc.in_features

        # 移除原始 ResNet18 的最后一个全连接层
        modules = list(self.resnet.children())[:-1]
        self.resnet = nn.Sequential(*modules)

        # 添加一个新的全连接层，输出3个值 (x, y, z)
        self.fc = nn.Linear(num_features, out_features)

    def forward(self, x):
        # 如果图像是2通道的，复制一个通道以适应3通道输入
        if x.size(1) == 2:
            x = torch.cat((x, x[:, :1, :, :]), dim=1)

        # 通过 ResNet 提取特征
        x = self.resnet(x)
        x = torch.flatten(x, 1)

        # 通过全连接层得到最终的坐标
        x = self.fc(x)
        return x
       
class DeepONet(nn.Module):
    def __init__(self, out_features, trunk_layers):
        super(DeepONet, self).__init__()
        # if isinstance(branch, list):
        #     self.branch_net = MLP(branch, type='relu', last_activation=False)
        # else:
        #     self.branch_net = branch
        self.branch_net = ResNet(out_features)
        
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
        
            train_mse /= len(data_train.dataset)
            train_l2/= len(data_train.dataset)
            test_l2 /= len(data_test.dataset)
        
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
        np.savetxt(f'../log/{formatted_time}.txt', y_pred.cpu().detach().numpy())
        return y_pred.cpu().detach().numpy()
    
    def save(self, filename):
        current_time = datetime.datetime.now()
        formatted_time = current_time.strftime('%Y-%m-%d_%H-%M-%S')
        torch.save(self.model, f'{filename}{formatted_time}.pth')
        
class Simple2DCNN(nn.Module):
    def __init__(self):
        super(Simple2DCNN, self).__init__()
        # Define the layers of the CNN
        self.conv1 = nn.Conv2d(in_channels=2, out_channels=8, kernel_size=3, stride=1, padding=1)
        self.pool = nn.MaxPool2d(kernel_size=2, stride=2, padding=0)
        self.conv2 = nn.Conv2d(in_channels=8, out_channels=16, kernel_size=3, stride=1, padding=1)
        self.fc1 = nn.Linear(in_features=16 * 32 * 32, out_features=120)
        self.fc2 = nn.Linear(in_features=120, out_features=60)
        self.fc3 = nn.Linear(in_features=60, out_features=3)  # Output 3D coordinates (x, y, z)

    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))
        x = self.pool(F.relu(self.conv2(x)))
        x = x.view(-1, 16 * 32 * 32)  # Flatten the tensor for the fully connected layer
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = self.fc3(x)  # Output layer
        return x

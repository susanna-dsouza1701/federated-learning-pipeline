# -*- coding: utf-8 -*-
import torch
import time
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torchvision import datasets, transforms

import numpy as np
import threading
import multiprocessing

import syft as sy 
hook = sy.TorchHook(torch)
  
#from mnist.mnist_settings import MNIST_Settings
from stroke_data.settings import Stroke_Settings
from stroke_data.settings import Net

#args = MNIST_Settings()
args = Stroke_Settings()

# dev settings
use_cuda = not args.no_cuda and torch.cuda.is_available()
torch.manual_seed(args.seed)
device = torch.device("cuda" if use_cuda else "cpu")
kwargs = {'num_workers': 1, 'pin_memory': True} if use_cuda else {}

# initialize virtual workers
vworker_comp = []

for worker in range(args.vworkers):
  id_string = "worker" + str(worker)
  vworker_comp.append(sy.VirtualWorker(hook, id=id_string))

print("{} virtual workers initialized".format(args.vworkers))

# initialize train and test dataset
train_dataset, test_dataset = args.gen_data()

train_loader = torch.utils.data.DataLoader(
    train_dataset,
    batch_size=args.batch_size, shuffle=True, **kwargs)

test_loader = torch.utils.data.DataLoader(
    test_dataset,
    batch_size=args.test_batch_size, shuffle=True, **kwargs)

print("train and test dataset loaded")

# federate dataset
remote_dataset = []
for worker in range(args.vworkers):
  remote_dataset.append(list())

remote_dataset = args.datafed_method(remote_dataset, train_loader, vworker_comp)

print("dataset federated")

# performs a round of training on local worker if available
def update(args, model, device, data, target, optimizer):
    model.train()
    model.send(data.location)
    data, target = data.to(device), target.to(device)
    optimizer.zero_grad()
    output = model(data)
    loss = args.loss(output, target)
    loss.backward()
    optimizer.step()
    model.get()
    return model, loss

def train_fn (model, device, data, target, vworker_optimizers, epoch):
    # update remote models
    worker_idx = int(data.location.id[-1])      
    vworker_models[worker_idx], loss = update(args, model, device, data, target, vworker_optimizers[worker_idx])
    return

def train(args, vworker_models, device, remote_dataset, vworker_optimizers, vworker_avail, epoch):
    print('{} batches'.format(len(remote_dataset[0])-1))
    
    for data_index in range(len(remote_dataset[0])-1): # batch
       
        for i in range(args.vworkers): # worker
            if(vworker_avail[i] < 0.5): # non-availability
                #print("worker " + str(i) + " not available")
                continue # don't calculate gradient update for this data
                        
            data, target = remote_dataset[i][data_index]
            train_fn(vworker_models[i], device, data, target, vworker_optimizers, epoch)
        
        if data_index % args.log_interval == 0:
            print('batch {} complete'.format(data_index))

# test code
def test(args, model, device, test_loader):
    model.eval()
    test_loss = 0
    correct = 0
    with torch.no_grad():
        for data, target in test_loader:
            data, target = data.to(device), target.to(device)
            output = model(data)
            test_loss += args.loss(output, target, reduction='sum').item() # sum up batch loss
            pred = output.argmax(1, keepdim=True) # get the index of the max log-probability 
            correct += pred.eq(target.view_as(pred)).sum().item()

    test_loss /= len(test_loader.dataset)

    print('\nTest set: Average loss: {:.4f}, Accuracy: {}/{} ({:.0f}%)\n'.format(
        test_loss, correct, len(test_loader.dataset),
        100. * correct / len(test_loader.dataset)))

# create remote models, parameters, optimizers
vworker_models, vworker_params, vworker_optimizers = args.gen_local(device)

# training pipeline
start = time.time()

for epoch in range(1, args.epochs + 1):
    vworker_avail = np.random.choice(2, args.vworkers, p=[1-args.p_available, args.p_available])
    print('Epoch {} Availability:\n{}'.format(epoch, vworker_avail))

    for i in range(args.local_epochs):
        train(args, vworker_models, device, remote_dataset, vworker_optimizers, vworker_avail, epoch) # train
    vworker_models, vworker_params = args.aggregate(vworker_models, device, vworker_params, vworker_avail) # aggregate
    test(args, vworker_models[0], device, test_loader) # test

end = time.time()
print('Time elapsed: {} sec'.format(end - start))


if (args.save_model):
    torch.save(model.state_dict(), "mnist_cnn.pt")

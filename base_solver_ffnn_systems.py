"""
base solver for transfer ode (first order methods)
"""
import torch
import torch.nn as nn
import argparse
import torch.optim as optim
import numpy as np
import time
from sklearn.decomposition import PCA
from sklearn.preprocessing import MinMaxScaler
from torchdiffeq import odeint_adjoint as odeint
from mpl_toolkits.mplot3d import Axes3D
import random

parser = argparse.ArgumentParser('transfer demo')

parser.add_argument('--tmax', type=float, default=6.)
parser.add_argument('--dt', type=int, default=0.1)
parser.add_argument('--niters', type=int, default=10000)
parser.add_argument('--niters_test', type=int, default=15000)
parser.add_argument('--hidden_size', type=int, default=100)
parser.add_argument('--num_bundles', type=int, default=20)
parser.add_argument('--num_bundles_test', type=int, default=1000)
parser.add_argument('--test_freq', type=int, default=100)
parser.add_argument('--viz', action='store_false')
parser.add_argument('--gpu', type=int, default=0)
parser.add_argument('--evaluate_only', action='store_true')
args = parser.parse_args()
scaler = MinMaxScaler()

# print(args.evaluate_only==False)

class diffeq(nn.Module):
    """
    defines the diffeq of interest
    """

    def __init__(self):
        super().__init__()
        # self.a1 = a1
        self.Amatrix = torch.tensor([[0,1],[-1,0]])
    # return ydot
    def forward(self, t, y):
        return get_udot(y)

def get_udot(y):
    Amatrix = torch.tensor([[0.5, 1.], [-1., 0.]])
    yd = Amatrix @ y.t()
    return yd.t()


class base_diffeq:
    """
    integrates base_solver given y0 and time
    """

    def __init__(self, base_solver):
        self.base = base_solver

    def get_solution(self, true_y0, t):
        with torch.no_grad():
            true_y = odeint(self.base, true_y0, t, method='dopri5')
        return true_y

    def get_deriv(self, true_y0, t):
        with torch.no_grad():
            true_ydot = self.base(t, true_y0)
        return true_ydot


class estim_diffeq:
    """
    integrates base_solver given y0 and time
    """

    def __init__(self, base_solver):
        self.base = base_solver

    def get_solution(self, true_y0, t):
        with torch.no_grad():
            true_y = odeint(self.base, true_y0, t, method='midpoint')
        return true_y

    def get_deriv(self, true_y0, t):
        with torch.no_grad():
            true_ydot = self.base(t, true_y0)
        return true_ydot


class ODEFunc(nn.Module):
    """
    function to learn the outputs u(t) and hidden states h(t) s.t. u(t) = h(t)W_out
    """

    def __init__(self, hidden_dim, output_dim):
        super(ODEFunc, self).__init__()
        self.hdim = hidden_dim
        self.nl = nn.Tanh()
        self.lin1 = nn.Linear(1, self.hdim)
        self.lin2 = nn.Linear(self.hdim, self.hdim)
        self.lout = nn.Linear(self.hdim, output_dim, bias=False)

    def forward(self, t):
        x = self.h(t)
        x = self.lout(x)
        return x

    def wouts(self, x):
        return self.lout(x)

    def h(self, t):
        x = self.lin1(t)
        x = self.nl(x)
        x = self.lin2(x)
        x = self.nl(x)
        return x


def diff(u, t, order=1):
    # code adapted from neurodiffeq library
    # https://github.com/NeuroDiffGym/neurodiffeq/blob/master/neurodiffeq/neurodiffeq.py
    """The derivative of a variable with respect to another.
    """
    # ones = torch.ones_like(u)

    der = torch.cat([torch.autograd.grad(u[:, i].sum(), t, create_graph=True)[0] for i in range(u.shape[1])], 1)
    if der is None:
        print('derivative is None')
        return torch.zeros_like(t, requires_grad=True)
    else:
        der.requires_grad_()
    for i in range(1, order):

        der = torch.cat([torch.autograd.grad(der[:, i].sum(), t, create_graph=True)[0] for i in range(der.shape[1])], 1)
        # print()
        if der is None:
            print('derivative is None')
            return torch.zeros_like(t, requires_grad=True)
        else:
            der.requires_grad_()
    return der


class Transformer_Learned(nn.Module):
    """
    returns Wout learnable, only need hidden and output dims
    """

    def __init__(self, input_dims, output_dims):
        super(Transformer_Learned, self).__init__()
        self.lin1 = nn.Linear(args.hidden_size, output_dims)

    def forward(self, x):
        return self.lin1(x)



def get_wout(s, sd,sdd, y0,y0dot,m1,m2,k1,k2, t):


    Lmat = torch.tensor([[m1, 0.], [0., m2]])
    Rmat = torch.tensor([[k1 + k2, -k2], [-k2, k1 + k2]])

    Amatrix = torch.linalg.inv(Lmat)@Rmat

    y0 = torch.stack([y0 for _ in range(len(s))]).reshape(len(s), -1)

    hddothat = torch.block_diag(sdd,sdd)
    hdothat = torch.block_diag(sd,sd)
    hhat = torch.block_diag(s,s)


    Amatrixhat = torch.zeros((hdothat.shape[0],hdothat.shape[0]))
    # print(Amatrix.shape)
    for i in range(Amatrix.shape[0]):
        for j in range(Amatrix.shape[1]):
            # print(Amatrix[i,j])
            Amatrixhat[i*s.shape[0]:(i+1)*s.shape[0],j*s.shape[0]:(j+1)*s.shape[0]]=torch.eye(s.shape[0],s.shape[0])*Amatrix[i,j]


    # top = torch.cat([torch.zeros_like(s),s],1)
    # bottom = torch.cat([-s, torch.zeros_like(s)], 1)
    # rhs = torch.cat([top,bottom],0)
    DH = hddothat + Amatrixhat@hhat

    h0= torch.block_diag(s[0,:].reshape(1,-1),s[0,:].reshape(1,-1))

    h0dot = torch.block_diag(sd[0, :].reshape(1, -1), sd[0, :].reshape(1, -1))

    W0 = torch.linalg.solve(DH.t()@DH + h0.t()@h0 + h0dot.t()@h0dot, h0.t()@(y0[0,:].reshape(-1,1)) + h0dot.t()@(y0dot[0,:].reshape(-1,1)) )
    return W0

    # print(f's {s.shape}')
    # snew = torch.stack([s,s],0)
    # # print(f'snew {snew.shape}')
    #
    # Amatrix = torch.tensor([[0., 1.], [-1., 0.]])
    # rhs =  torch.stack([-s,s],0)
    # # print(f'rhs:{rhs.shape}')
    #
    # lhs = torch.stack([sd,sd],0)
    #
    # DH = lhs - rhs
    # h0m = torch.stack([s[0,:].reshape(-1, 1),s[0,:].reshape(-1,1)],0)
    #
    # # print(f'dhtdh:{torch.matmul(torch.transpose(DH,1,2), DH).shape}')
    #
    #
    # W0 = torch.linalg.solve(torch.matmul(torch.transpose(DH,1,2), DH) + torch.matmul(h0m, torch.transpose(h0m,1,2)), torch.matmul(h0m,y0[0,:].reshape(2,1,1)) )
    # return W0
import matplotlib.pyplot as plt
if args.viz:


    fig = plt.figure(figsize=(12, 4), facecolor='white')
    ax_traj = fig.add_subplot(131, frameon=False)
    ax_phase = fig.add_subplot(132, frameon=False)
    ax_vecfield = fig.add_subplot(133, frameon=False)
    plt.show(block=False)


def visualize(true_y, pred_y, lst):
    if args.viz:
        ax_traj.cla()
        ax_traj.set_title('Trajectories')
        ax_traj.set_xlabel('t')
        ax_traj.set_ylabel('x,y')
        for i in range(2):
            ax_traj.plot(t.detach().cpu().numpy(), true_y.cpu().numpy()[:, i],
                         'g-')
            ax_traj.plot(t.detach().cpu().numpy(), pred_y.cpu().numpy()[:, i], '--', 'b--')
        ax_phase.set_yscale('log')
        ax_phase.plot(np.arange(len(lst)), lst)

        ax_traj.legend()

        plt.draw()
        plt.pause(0.001)



def get_m(x_in,m1,m2):
    Amatrix = torch.tensor([[m1, 0.], [0., m2]])
    output = Amatrix @ x_in.t()
    return output.t()

def get_k(x_in,k1,k2):
    Amatrix = torch.tensor([[k1+k2, -k2], [-k2, k1+k2]])
    output = Amatrix @ x_in.t()
    return output.t()



if __name__ == '__main__':

    ii = 0
    NDIMZ = args.hidden_size

    r2 = 1.5
    r1 = 0.5

    #true_y0 = (r2 - r1) * torch.rand(2) + r1
    true_y0 = torch.tensor([1.,1.]).reshape(1,2)
    true_y0dot = torch.tensor([1., 3.]).reshape(1, 2)


    t = torch.arange(0., args.tmax, args.dt).reshape(-1, 1)
    t.requires_grad = True


    diffeq_init = diffeq()
    gt_generator = base_diffeq(diffeq_init)

    # true_y = gt_generator.get_solution(true_y0.reshape(1,2),t.ravel()).reshape(-1,2)

    # use this quick test to find gt solutions and check training ICs
    # have a solution (don't blow up for dopri5 integrator)
    true_y = gt_generator.get_solution(true_y0.reshape(-1, 2), t.ravel()).reshape(-1,2)

    # instantiate wout with coefficients
    func = ODEFunc(hidden_dim=NDIMZ, output_dim=2)

    optimizer = optim.Adam(func.parameters(), lr=1e-3, weight_decay=1e-6)

    loss_collector = []

    if not args.evaluate_only:

        for itr in range(1, args.niters + 1):
            func.train()

            # add t0 to training times, including randomly generated ts
            t0 = torch.tensor([[0.]])
            t0.requires_grad = True
            tv = args.tmax * torch.rand(int(args.tmax / args.dt)).reshape(-1, 1)
            tv.requires_grad = True
            tv = torch.cat([t0, tv], 0)
            optimizer.zero_grad()

            # compute hwout,hdotwout
            pred_y = func(tv)
            pred_ydot = diff(pred_y,tv)
            pred_yddot = diff(pred_ydot, tv,1)

            # enforce diffeq
            loss_diffeq = get_m(pred_yddot,m1=1.,m2=1.) + get_k(pred_y,k1=0.5,k2=0.5)
            # loss_diffeq = (a1(tv.detach()).reshape(-1, 1)) * pred_ydot + (a0(tv.detach()).reshape(-1, 1)) * pred_y - f(
            #     tv.detach()).reshape(-1, 1)

            # enforce initial conditions
            loss_ics = (pred_y[0, :].ravel() - true_y0.ravel()) + (pred_ydot[0, :].ravel() - true_y0dot.ravel())

            loss = torch.mean(torch.square(loss_diffeq)) + torch.mean(torch.square(loss_ics))
            loss.backward()
            optimizer.step()
            loss_collector.append(torch.square(loss_diffeq).mean().item())
            if itr % args.test_freq == 0:
                func.eval()
                pred_y = func(t).detach()
                pred_y = pred_y.reshape(-1, 2)
                visualize(true_y.detach(), pred_y.detach(), loss_collector)
                ii += 1

        torch.save(func.state_dict(), 'func_ffnn_systems_coupled')

    # with torch.no_grad():

    r2 = 1.5
    r1 = 0.5

    # true_y0 = (r2 - r1) * torch.rand(2) + r1
    true_y0 = torch.tensor([1., 1.]).reshape(1, 2)

    true_y0dot = torch.tensor([1., 3.]).reshape(1, 2)

    t = torch.arange(0., args.tmax, args.dt).reshape(-1, 1)
    t.requires_grad = True

    diffeq_init = diffeq()
    gt_generator = base_diffeq(diffeq_init)


    func.load_state_dict(torch.load('func_ffnn_systems_coupled'))
    func.eval()


    h = func.h(t)
    hd = diff(h, t)
    hdd = diff(hd,t)
    h = h.detach()
    hd = hd.detach()
    hdd = hdd.detach()

    plt.figure()

    plt.plot(h)
    plt.show()



    gz_np = h.detach().numpy()
    T = np.linspace(0, 1, len(gz_np)) ** 2
    new_hiddens = scaler.fit_transform(gz_np)
    pca = PCA(n_components=3)
    pca_comps = pca.fit_transform(new_hiddens)

    fig = plt.figure()
    ax = plt.axes(projection='3d')

    if pca_comps.shape[1] >= 2:
        s = 10  # Segment length
        for i in range(0, len(gz_np) - s, s):
            ax.plot3D(pca_comps[i:i + s + 1, 0], pca_comps[i:i + s + 1, 1], pca_comps[i:i + s + 1, 2],
                      color=(0.1, 0.8, T[i]))
            plt.xlabel('comp1')
            plt.ylabel('comp2')


    s1 = time.time()

    m1 = 5.
    m2 = 1.
    k1 = 0.5
    k2 = 0.5

    wout = get_wout(h, hd,hdd, true_y0,true_y0dot,m1,m2,k1,k2, t.detach())

    # print(wout)
    # print(f'wout {wout.shape}')
    # woutn = wout.reshape(2,100)
    # wout = woutn.t()
    nwout = torch.cat([wout[:args.hidden_size,0].reshape(-1,1),wout[args.hidden_size:,0].reshape(-1,1)],1)


    # print(wout)
    pred_y = h @ nwout
    pred_yddot = hdd@nwout

    print('final loss')
    print(((get_m(pred_yddot,m1,m2)+get_k(pred_y,k1,k2))**2).mean())

    # print(f'predy:{pred_y}')
    s2 = time.time()
    print(f'all_ics:{s2 - s1}')
    # print(pred_y)
    s1 = time.time()
    true_ys = (gt_generator.get_solution(true_y0, t.ravel())).reshape(-1, 2)
    s2 = time.time()
    print(f'gt_ics:{s2 - s1}')

    # print(true_ys.shape,pred_y.shape)

    # s1 = time.time()
    # true_y = estim_generator.get_solution(ics.reshape(-1, 1), t.ravel())
    # estim_ys = true_y.reshape(len(pred_y), ics.shape[1])
    # s2 = time.time()
    # print(f'estim_ics:{s2 - s1}')

    # print(f'prediction_accuracy:{((pred_y - true_ys) ** 2).mean()} pm {((pred_y - true_ys) ** 2).std()}')
    # print(f'estim_accuracy:{((estim_ys - true_ys) ** 2).mean()} pm {((estim_ys - true_ys) ** 2).std()}')

    fig, ax = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    # print(true_ys[0,:])
    for i in range(0,2):
        # ax[0].plot(t.detach().cpu().numpy(), true_ys.cpu().numpy()[:, i], c='blue', linestyle='dashed')
        ax[0].plot(t.detach().cpu().numpy(), pred_y.cpu().numpy()[:, i], c='orange')
        # plt.draw()

    ax[1].plot(t.detach().cpu().numpy(), ((true_ys - pred_y) ** 2).mean(1).cpu().numpy(), c='green')
    ax[1].set_xlabel('Time (s)')
    plt.legend()
    plt.show()
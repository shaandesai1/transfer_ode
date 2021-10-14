"""
base solver for transfer ode (first order methods)
"""
import torch
import torch.nn as nn
import argparse
import torch.optim as optim
import numpy as np
import time
import seaborn as sns
from matplotlib import ticker, cm

torch.manual_seed(33)

parser = argparse.ArgumentParser('transfer demo')

parser.add_argument('--tmax', type=float, default=3.)
parser.add_argument('--dt', type=int, default=0.1)
parser.add_argument('--niters', type=int, default=40000)
parser.add_argument('--niters_test', type=int, default=15000)
parser.add_argument('--hidden_size', type=int, default=100)
parser.add_argument('--num_ics', type=int, default=1)
parser.add_argument('--num_test_ics', type=int, default=1000)
parser.add_argument('--test_freq', type=int, default=200)
parser.add_argument('--bs', type=int, default=100)

parser.add_argument('--viz', action='store_true')
parser.add_argument('--gpu', type=int, default=0)
parser.add_argument('--evaluate_only', action='store_false')
import matplotlib.pyplot as plt
args = parser.parse_args()
# from torchdiffeq import odeint_adjoint as odeint

# torch.set_default_tensor_type('torch.cuda.FloatTensor')
# torch.backends.cudnn.benchmark = True

torch.set_default_tensor_type('torch.DoubleTensor')

class SiLU(nn.Module):
    def __init__(self):
        '''
        Init method.
        '''
        super().__init__() # init the base class

    def forward(self, input):
        '''
        Forward pass of the function.
        '''
        return torch.sin(input)

class ODEFunc(nn.Module):
    """
    function to learn the outputs u(t) and hidden states h(t) s.t. u(t) = h(t)W_out
    """

    def __init__(self, hidden_dim,output_dim):
        super(ODEFunc, self).__init__()
        self.hdim = hidden_dim
        self.nl1 = SiLU()
        # self.nl2 = nn.Tanh()
        self.lin1 = nn.Linear(2, self.hdim)
        self.lin2 = nn.Linear(self.hdim, self.hdim)
        # self.lin3 = nn.Linear(self.hdim, self.hdim)
        # self.weight_2 = nn.Parameter(torch.zeros(1))

        self.lout = nn.Linear(self.hdim, output_dim, bias=True)

    def hidden_states(self, t,x):
        inputs_ = torch.cat([t.reshape(-1,1),x.reshape(-1,1)],1)
        u = self.lin1(inputs_)
        u = self.nl1(u)
        u = self.lin2(u)
        u = self.nl1(u)
        return u

        # u = self.nl(u)
        # return u

    def forward(self, t,x):
        u = self.hidden_states(t,x)
        u = self.lout(u)
        return u

    def wouts(self, x):
        return self.lout(x)

def diff(u, t, order=1):
    # code adapted from neurodiffeq library
    # https://github.com/NeuroDiffGym/neurodiffeq/blob/master/neurodiffeq/neurodiffeq.py
    r"""The derivative of a variable with respect to another.
    While there's no requirement for shapes, errors could occur in some cases.
    See `this issue <https://github.com/NeuroDiffGym/neurodiffeq/issues/63#issue-719436650>`_ for details
    :param u: The :math:`u` in :math:`\displaystyle\frac{\partial u}{\partial t}`.
    :type u: `torch.Tensor`
    :param t: The :math:`t` in :math:`\displaystyle\frac{\partial u}{\partial t}`.
    :type t: `torch.Tensor`
    :param order: The order of the derivative, defaults to 1.
    :type order: int
    :returns: The derivative evaluated at ``t``.
    :rtype: `torch.Tensor`
    """
    # ones = torch.ones_like(u)


    der = torch.cat([torch.autograd.grad(u[:, i].sum(), t, create_graph=True)[0] for i in range(u.shape[1])],1)
    if der is None:
        print('derivative is None')
        return torch.zeros_like(t, requires_grad=True)
    else:
        der.requires_grad_()
    for i in range(1, order):

        der = torch.cat([torch.autograd.grad(der[:, i].sum(), t, create_graph=True)[0] for i in range(der.shape[1])],1)
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


class Transformer_Analytic(nn.Module):
    """
    returns Wout analytic, need to define the parameter coefficients
    """

    def __init__(self,bb,tb,lbc,rbc,rho):
        super(Transformer_Analytic, self).__init__()
        self.bb = bb
        self.tb = tb
        self.lbc = lbc
        self.rbc = rbc
        self.rho = rho
        # self.lambda_ = lambda_


    def append_ones(self,var,type='ones'):

        if type == 'ones':
            return torch.cat([var,torch.ones(len(var),1)],1)

        else:
            return torch.cat([var,torch.zeros(len(var),1)],1)
    def get_wout(self, func,t,x,grid_t,grid_x,ks):

        zindices = np.random.choice(len(t), 500,replace=False)
        # print(zindices)
        t = t[zindices, :].reshape(-1, 1)
        x = x[zindices, :].reshape(-1, 1)

        # print('enter wout')
        # s1=time.time()
        print('a')
        H = func.hidden_states(t,x)#torch.cat([func.hidden_states(t,x),torch.ones(len(t),1)],1)
        print('b')
        # aa = diff(H,t)
        d2Hdt2 = torch.cat([diff(H,t,2),torch.zeros(len(H),1)],1)
        print('c')
        # bb = diff(H,x)
        d2Hdx2 =torch.cat([diff(H,x,2),torch.zeros(len(H),1)],1)

        # H = torch.cat([H,torch.ones(len(H),1)],1)
        # H = torch.cat([H,torch.ones(len(H),1)],1)
        # s2 = time.time()
        # print(s2-s1)
        rho = self.rho(t.reshape(-1,1),x.reshape(-1,1))#torch.cat([get_rho(t.reshape(-1,1),x.reshape(-1,1),ks_val,0,ks_val,0).reshape(-1,1) for ks_val in ks],1)

        DH = (d2Hdt2+d2Hdx2)


        xindices = np.random.choice(len(grid_t),150,replace=False)

        H0 = func.hidden_states(grid_t[xindices,0].reshape(-1,1),grid_x[xindices,0].reshape(-1,1))
        H0 = self.append_ones(H0)

        HT = func.hidden_states(grid_t[xindices, -1].reshape(-1, 1), grid_x[xindices, -1].reshape(-1, 1))
        HT = self.append_ones(HT)

        HL = func.hidden_states(grid_t[0,xindices].reshape(-1,1),grid_x[0,xindices].reshape(-1,1))
        HL = self.append_ones(HL)
        HR = func.hidden_states(grid_t[-1, xindices].reshape(-1, 1), grid_x[-1, xindices].reshape(-1, 1))
        HR = self.append_ones(HR)

        BB = self.bb(grid_x[xindices,0]).reshape(-1,1)
        TB = self.tb(grid_x[xindices, -1]).reshape(-1, 1)
        BL = self.lbc(grid_t[0,xindices]).reshape(-1,1)
        BR = self.rbc(grid_t[-1,xindices]).reshape(-1,1)

        LHS = DH.t() @ DH +2*torch.eye(len(DH.t()))+ H0.t() @ H0 + HT.t() @ HT + HL.t() @ HL + HR.t() @ HR
        RHS = DH.t()@rho + H0.t()@BB + HT.t()@TB + HL.t()@BL + HR.t()@BR
        # print(torch.linalg.cond(LHS))

        W0solve = torch.linalg.solve(LHS, RHS)
        # return W0,d2Hdt2,d2Hdx2

        new_mat_A = torch.cat([DH,H0,HT,HL,HR,2*torch.eye(len(DH.t()))],0)
        new_mat_Y = torch.cat([rho,torch.ones(len(H0),1)*0,torch.ones(len(HT),1)*0,torch.ones(len(HL),1)*0,torch.ones(len(HR),1)*0,torch.ones(len(DH.t()),1)*0],0)
        W0 = torch.linalg.lstsq(new_mat_A,new_mat_Y)#torch.linalg.solve(LHS, DH.t()@rho + H0.t()@BB + HT.t()@TB + HL.t()@BL + HR.t()@BR)
        return W0.solution,W0solve





if args.viz:
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(12, 4), facecolor='white')
    ax_traj = fig.add_subplot(141, frameon=False)
    ax_phase = fig.add_subplot(142, frameon=False)
    ax_vecfield = fig.add_subplot(143, frameon=False)
    ax_vecfield2 = fig.add_subplot(144, frameon=False)

    plt.show(block=False)


def visualize(u,t,x,grid_t,grid_x,lst):
    if args.viz:
        ax_traj.cla()
        ax_phase.cla()
        ax_traj.contourf(x,t,u[:,0].reshape(len(x),len(t)).t())
        # u_true = torch.sin(grid_x)*torch.exp(-grid_t)
        ax_phase.contourf(x,t,u[:,1].reshape(len(x),len(t)).t())
        ax_vecfield.contourf(x, t, u[:, 2].reshape(len(x), len(t)).t())
        # ax_vecfield2.contourf(x, t, u[:, 3].reshape(len(x), len(t)).t())
        # ax_vecfield.contourf(x, t, u[:, 2].reshape(len(x), len(t)).t())

        # ax_phase.contourf(x,t,u[:,2].reshape(len(x),len(t)).t())
        # ax_vecfield.contourf(x, t, u[:, 4].reshape(len(x), len(t)).t())
        # ax_phase.contourf(x,t,u[:,2].reshape(len(x),len(t)).t())
        # ax_vecfield.contourf(x, t, u[:, 4].reshape(len(x), len(t)).t())
        # #
        # # ax_vecfield.contourf(x, t, (u_true.reshape(len(x), len(t)).t()-u.reshape(len(x),len(t)).t())**2)
        # ax_traj.legend()
        plt.draw()
        plt.pause(0.001)


def u_analytic(x,y,k):
    return -(torch.sin(np.pi*k*x)*torch.sin(np.pi*k*y))/(2*(k*np.pi)**2)



def get_rho(t,x,x01,y01,x02,y02):
    X=x
    Y=t
    # A = 10;
    # # x0 = xmid;
    # # y0 = tmid;
    # theta = torch.tensor(0.)
    # sigma_X = 0.1
    # sigma_Y = 0.1
    # radius = 0.2
    # rho = torch.zeros_like(X)
    # for j in range(len(x)):
    #     if (X[j]-x01)**2 + (Y[j]-y01)**2 <radius**2:
    #         rho[j] = -10.
    #     elif (X[j]-x02)**2 + (Y[j]-y02)**2 <radius**2:
    #         rho[j] = -10.
    #     else:
    #         pass
    #
    # tf1 = (X - x01) ** 2 + (Y - y01) ** 2 < radius ** 2
    # tf2 = (X - x02) ** 2 + (Y - y02) ** 2 < radius ** 2

    return torch.sin(x01*np.pi*X)*torch.sin(x02*np.pi*Y)#(-10*(tf1)-10*tf2)
    #
    # return rho
    # theta = torch.tensor(theta)
    #
    # # X, Y = np.meshgrid(np.arange(-5,5,.1), np.arange(-5,5,.1))
    # a = torch.cos(theta) ** 2 / (2 * sigma_X ** 2) + torch.sin(theta) ** 2 / (2 * sigma_Y ** 2);
    # b = -torch.sin(2 * theta) / (4 * sigma_X ** 2) + torch.sin(2 * theta) / (4 * sigma_Y ** 2);
    # c = torch.sin(theta) ** 2 / (2 * sigma_X ** 2) + torch.cos(theta) ** 2 / (2 * sigma_Y ** 2);
    #
    # Z1 = A * torch.exp(-(a * (X - (x01)) ** 2 + 2 * b * (X - (x01)) * (Y - y01) + c * (Y - y01) ** 2));
    # Z2 = A * torch.exp(-(a * (X - (x02)) ** 2 + 2 * b * (X - (x02)) * (Y - y02) + c * (Y - y02) ** 2));
    # #
    # return Z1 + Z2
    # # plt.contour(X, Y, Z);


if __name__ == '__main__':

    ii = 0
    NDIMZ = args.hidden_size
    xl = 0.#-2*np.pi
    xr = 1.#2*np.pi#torch.tensor(np.pi)
    t0 = 0.#-2*np.pi
    tmax = 1.#2*np.pi#torch.tensor(np.pi)
    x_evals = torch.linspace(xl,xr,100)
    y_evals = torch.linspace(t0,tmax,100)
    grid_x, grid_t = torch.meshgrid(x_evals, y_evals)
    grid_x.requires_grad = True
    grid_t.requires_grad = True

    # print(grid_t[:,0], grid_x[:,0])
    # print(grid_x)
    grid_x = torch.ravel(grid_x)
    grid_t = torch.ravel(grid_t)


    # left BC
    bc_left = torch.ones(100)*xl
    bc_right = torch.ones(100)*xr
    ic_t0 = torch.ones(100)*t0
    ic_tmax = torch.ones(100)*tmax

    bc_left.requires_grad=True
    bc_right.requires_grad=True
    ic_t0.requires_grad=True
    ic_tmax.requires_grad=True
    # wout_gen = Transformer_Analytic()
    func = ODEFunc(hidden_dim=NDIMZ,output_dim=4)
    optimizer = optim.Adam(func.parameters(), lr=1e-3)

    center_xs = torch.tensor([[1.,1.],[2.,2.],[3.,3.],[4.,4.],[1.,1.]])#(10.+10.)*torch.rand(5,2) + -10.
    center_ys = torch.zeros_like(center_xs)#(7.+7.)*torch.rand(5,2) + -7.

    loss_collector = []
    best_residual = 1e-1
    if not args.evaluate_only:

        for itr in range(1, args.niters + 1):
            func.train()
            indices = torch.tensor(np.random.choice(len(grid_x),1000,replace=False))
            x_tr = (grid_x[indices]).reshape(-1,1) + 0.005*torch.rand(len(indices),1)
            t_tr = (grid_t[indices]).reshape(-1,1) + 0.005*torch.rand(len(indices),1)

            # add t0 to training times, including randomly generated ts
            optimizer.zero_grad()

            u = func(t_tr,x_tr)
            d2udt2 = diff(u,t_tr,2)
            d2udx2 = diff(u, x_tr, 2)

            # kvals = torch.tensor([0.,0.,0.,0.,0.]).reshape(1,-1)
            rho1 = get_rho(t_tr,x_tr,center_xs[0,0],center_ys[0,0],center_xs[0,1],center_ys[0,1])
            rho2 = get_rho(t_tr, x_tr, center_xs[1,0],center_ys[1,0],center_xs[1,1],center_ys[1,1])
            rho3 = get_rho(t_tr, x_tr, center_xs[2,0],center_ys[2,0],center_xs[2,1],center_ys[2,1])
            rho4 = get_rho(t_tr, x_tr, center_xs[3,0],center_ys[3,0],center_xs[3,1],center_ys[3,1])
            # rho5 = get_rho(t_tr, x_tr, center_xs[4,0],center_ys[4,0],center_xs[4,1],center_ys[4,1])

            # print(u.shape,rho1.shape)

            rhos = torch.cat([rho1,rho2,rho3,rho4],1)


            loss_diffeq = torch.mean((d2udt2 + d2udx2 -rhos)**2)

            u_t0 = torch.mean((func(ic_t0,x_evals.reshape(-1,1)).ravel() - 0)**2)
            u_tmax = torch.mean((func(ic_tmax, x_evals.reshape(-1, 1)).ravel() - 0) ** 2)
            u_left = torch.mean((func(y_evals.reshape(-1,1),bc_left.reshape(-1,1)) - 0) ** 2)
            u_right = torch.mean((func(y_evals.reshape(-1, 1), bc_right.reshape(-1,1)) - 0) ** 2)

            #enforce initial conditions
            loss_ics = u_t0 +u_tmax + u_left + u_right
            loss = loss_diffeq + loss_ics
            loss.backward()
            optimizer.step()

            print(loss_diffeq.item(),loss_ics.item())
            loss_collector.append(loss_diffeq.item())
            if itr % args.test_freq == 0:
                # with torch.no_grad():
                func.eval()
                # print(f'diffeq: {loss_collector[-1]}, bcs: {loss_ics.item()}')
                tvals = grid_t.reshape(-1, 1)
                xvals = grid_x.reshape(-1,1)
                u_eval = func(tvals,xvals)
                d2udt2 = diff(u_eval, tvals, 2)
                d2udx2 = diff(u_eval, xvals, 2)

                u_eval.detach_()
                d2udt2.detach_()
                d2udx2.detach_()

                # visualize(u_eval,y_evals,x_evals,grid_t,grid_x, loss_collector)

                rho1 = get_rho(tvals, xvals, center_xs[0, 0], center_ys[0, 0], center_xs[0, 1], center_ys[0, 1])
                rho2 = get_rho(tvals, xvals, center_xs[1, 0], center_ys[1, 0], center_xs[1, 1], center_ys[1, 1])
                rho3 = get_rho(tvals, xvals, center_xs[2, 0], center_ys[2, 0], center_xs[2, 1], center_ys[2, 1])
                rho4 = get_rho(tvals, xvals, center_xs[3, 0], center_ys[3, 0], center_xs[3, 1], center_ys[3, 1])
                # rho5 = get_rho(tvals, xvals, center_xs[4, 0], center_ys[4, 0], center_xs[4, 1], center_ys[4, 1])

                rhos = torch.cat([rho1, rho2, rho3,rho4], 1)

                loss_diffeq = torch.mean((d2udt2 + d2udx2 -rhos)**2)
                #
                current_residual = loss_diffeq.item()
                print(current_residual)
                if current_residual < best_residual:
                    torch.save(func.state_dict(), 'func_ffnn_helm_2')
                    best_residual = current_residual
                    print(itr, best_residual)
        # torch.save(func.state_dict(), 'func_ffnn_helm_2')

    # with torch.no_grad():
    rho = lambda v1,v2: (-1)**(2)*(2)*get_rho(v1,v2,1.,0,1.,0)/4. + (-1)**(3)*(2*2)*get_rho(v1,v2,2.,0,2.,0)/4. + (-1)**(4)*2*3*get_rho(v1,v2,3.,0,3.,0)/4.+(-1)**(5)*2*4*get_rho(v1,v2,4.,0,4.,0)/4.
    # rho = lambda v1,v2: []#(-1)**(2)*(2)*get_rho(v1,v2,1.,0,1.,0)/4. + (-1)**(3)*(2*2)*get_rho(v1,v2,2.,0,2.,0)/4. + (-1)**(4)*2*3*get_rho(v1,v2,3.,0,3.,0)/4.+(-1)**(5)*2*4*get_rho(v1,v2,4.,0,4.,0)/4.

    ft = lambda t: 0*t#3*torch.sin(t)
    fb = lambda t: 0*t#torch.sin(t)
    lbc = lambda t: 0*t#torch.sin(t)
    rbc = lambda t: 0*t#torch.cos(t)

    import matplotlib

    matplotlib.rcParams['text.usetex'] = True
    import matplotlib.pyplot as plt

    sns.axes_style(style='ticks')
    sns.set_context("paper", font_scale=3,
                    rc={"font.size": 30, "axes.titlesize": 25, "axes.labelsize": 30, "axes.legendsize": 20,
                        'lines.linewidth': 2.5})
    sns.set_palette('deep')
    sns.set_color_codes(palette='deep')


    wout_gen = Transformer_Analytic(fb,ft,lbc,rbc,rho)

    func.load_state_dict(torch.load('func_ffnn_helm_2',map_location=torch.device('cpu')))
    func.eval()

    x_evals = torch.linspace(xl, xr, 500)
    y_evals = torch.linspace(t0, tmax, 500)
    x_evals.requires_grad = True
    y_evals.requires_grad = True
    grid_x, grid_t = torch.meshgrid(x_evals, y_evals)


    # grid_x.requires_grad = True
    # grid_t.requires_grad = True


    kval = torch.tensor(0.)
    # print(grid_t[:,0], grid_x[:,0])

    x_evals1 = torch.linspace(xl+0.01, xr-0.01, 200)
    y_evals1 = torch.linspace(t0+0.01, tmax-0.01, 200)
    x_evals1.requires_grad = True
    y_evals1.requires_grad = True
    grid_x1, grid_t1 = torch.meshgrid(x_evals1, y_evals1)

    grid_xx = grid_x1.ravel()
    grid_tt = grid_t1.ravel()

    kvals = torch.linspace(1.,4.,100)

    t1 = time.time()
    WOUT,WOUT1 = wout_gen.get_wout(func,grid_t.reshape(-1,1),grid_x.reshape(-1,1),grid_t1,grid_x1,kvals)
    print(f'time:{time.time()-t1}')
    tv,xv = grid_t.reshape(-1, 1), grid_x.reshape(-1, 1)
    #
    H = func.hidden_states(tv,xv)
    # # Htt = diff(H,tv,2)
    # # Hxx = diff(H,xv,2)
    #
    H = torch.cat([H,torch.ones(len(H),1)],1)
    # Htt = torch.cat([Htt, torch.zeros(len(H), 1)], 1)
    # Hxx = torch.cat([Hxx, torch.zeros(len(H), 1)], 1)

    with torch.no_grad():
        out_pred = (H@WOUT).numpy()
        out_pred1 = (H@WOUT1).numpy()

        # np.save('out_pred.npy',out_pred)
        # np.save('out_pred1.npy',out_pred1)

        # out_pred = np.load('out_pred.npy')
        # out_pred1 = np.load('out_pred1.npy')

        # print((out_pred))
        # loss_test =Htt@WOUT + Hxx@WOUT -rho(tv,xv)
        # loss_test1 = Htt @ WOUT1 + Hxx @ WOUT1 - rho(tv, xv)

        # u_true = torch.cat([u_analytic(xv, tv, kv).reshape(-1,1) for kv in kvals],1)

        u_true = 1./4*(2*u_analytic(grid_x,grid_t,1)-4*u_analytic(grid_x,grid_t,2)+6*u_analytic(grid_x,grid_t,3)-8*u_analytic(grid_x,grid_t,4))

        s1 = np.transpose(out_pred.reshape(len(x_evals), len(y_evals)))
        s2 = np.transpose(out_pred1.reshape(len(x_evals), len(y_evals)))

        print(s1,s2,u_true.numpy())

        print(f'error:{((s1-u_true.numpy())**2).mean()}')
        print(f'error1:{((s2 - u_true.numpy()) ** 2).mean(),((s2 - u_true.numpy()) ** 2).std()}')

        # plt.figure()
        # contours = plt.contour(x_evals, y_evals, out_pred.reshape(len(x_evals), len(y_evals)).t(), 6, colors='black')
        # plt.clabel(contours, inline=True, fontsize=8)
        # plt.contourf(x_evals, y_evals, out_pred.reshape(len(x_evals), len(y_evals)).t(), alpha=0.9)
        # # fig.colorbar(pc,ax=ax[0])
        # plt.xlabel('x')
        # plt.ylabel('t')
        # plt.colorbar()
        #
        # plt.savefig('helm_2_qr.pdf',dpi=2400,bbox_inches='tight')
        #
        # fig, ax = plt.subplots(1,1, figsize=(10, 5))

        plt.figure()
        cmp=sns.color_palette("rocket", as_cmap=True)
        plt.contourf(x_evals, y_evals, np.transpose(out_pred.reshape(len(x_evals), len(y_evals))),levels=20,cmap=cmp)
        plt.xlabel(r'$x$')
        plt.ylabel(r'$y$')
        plt.colorbar()
        plt.savefig('helm_2_qr.pdf', dpi=2400, bbox_inches='tight')



        plt.figure(figsize=(8,6))
        cmp=sns.color_palette("rocket", as_cmap=True)
        plt.contourf(x_evals, y_evals, np.transpose(out_pred1.reshape(len(x_evals), len(y_evals))),levels=20,cmap=cmp)
        # plt.colorbar()
        plt.xlabel(r'$x$')
        plt.ylabel(r'$y$')
        plt.colorbar(format='%.0e')
        plt.savefig('helm_2_solver_v2.pdf', dpi=2400, bbox_inches='tight')

        plt.figure(figsize=(8,6))
        cmp = sns.color_palette("rocket", as_cmap=True)
        errf = (np.transpose(out_pred1.reshape(len(x_evals), len(y_evals)))-u_true.numpy())**2
        print(errf.min())
        plt.contourf(x_evals, y_evals,errf , cmap=cmp)
        # plt.colorbar()
        plt.xlabel(r'$x$')
        plt.ylabel(r'$y$')
        plt.colorbar(format='%.0e')
        plt.savefig('helm_2_solver_error.pdf', dpi=2400, bbox_inches='tight')




        # # pc = ax[1].contour(x_evals, y_evals, out_pred1.reshape(len(x_evals), len(y_evals)).t(),20)
        # # pc = ax[0].contour(x_evals, y_evals, out_pred.reshape(len(x_evals), len(y_evals)).t())
        #
        # ax[1].set_title('predicted solution')
        # fig.colorbar(pc, ax=ax[1])

        # u_true = 3*torch.sin(grid_xx) * torch.exp(-grid_tt)
        #
        # ax[1].contourf(x_evals, y_evals, u_true.reshape(len(x_evals), len(y_evals)).t())
        # ax[1].set_title('true solution')
        #
        # pc = ax[2].contourf(x_evals, y_evals, (u_true.reshape(len(x_evals), len(y_evals)).t() - out_pred.reshape(len(x_evals), len(y_evals)).t()) ** 2)
        # ax[2].set_title('residuals')
        # fig.colorbar(pc, ax=ax[2])

        # plt.show()
from threading import Thread
import torch as T
import torch.nn.functional as F
from agent import Agent

class MADDPG:
    def __init__(self, actor_dims, critic_dims, n_agents, n_actions, 
                 scenario='simple',  alpha=0.01, beta=0.01, fc1=64, 
                 fc2=64, gamma=0.99, tau=0.01, chkpt_dir='tmp/maddpg/'):
        self.agents = []
        self.n_agents = n_agents
        self.n_actions = n_actions
        chkpt_dir += scenario 
        for agent_idx in range(self.n_agents):
            self.agents.append(Agent(actor_dims[agent_idx], critic_dims,  
                            n_actions, n_agents, agent_idx, alpha=alpha, beta=beta,
                            chkpt_dir=chkpt_dir))

    def save_checkpoint(self):
        print('... saving checkpoint ...')
        for agent in self.agents:
            agent.save_models()

    def load_checkpoint(self):
        print('... loading checkpoint ...')
        for agent in self.agents:
            agent.load_models()

    def choose_action(self, raw_obs):
        actions = {}
        
        def _choose_action(agent_idx, agent, raw_obs):
            action = agent.choose_action(raw_obs[agent_idx])
            actions[agent_idx] = action
            
        threads = []
        for agent_idx, agent in enumerate(self.agents):
            thread = Thread(target=_choose_action, args=(agent_idx, agent, raw_obs), daemon=True)
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
            
        actions = [actions[i] for i in range(self.n_agents)]
        return actions

    def learn(self, memory):
        if not memory.ready():
            return

        actor_states, states, actions, rewards, \
        actor_new_states, states_, dones = memory.sample_buffer()

        device = self.agents[0].actor.device

        states = T.tensor(states, dtype=T.float32).to(device).clone().detach()
        actions = T.tensor(actions, dtype=T.float32).to(device)
        rewards = T.tensor(rewards).to(device)
        states_ = T.tensor(states_, dtype=T.float32).to(device)
        dones = T.tensor(dones).to(device)

        all_agents_new_actions = {}
        all_agents_new_mu_actions = {}
        old_agents_actions = {}
        
        def _get(agent_idx, agent):
            new_states = T.tensor(actor_new_states[agent_idx], 
                                 dtype=T.float32).to(device)
            new_pi = agent.target_actor.forward(new_states)
            all_agents_new_actions[agent_idx] = new_pi
            
            mu_states = T.tensor(actor_states[agent_idx], 
                                 dtype=T.float32).to(device)
            pi = agent.actor.forward(mu_states)
            
            all_agents_new_mu_actions[agent_idx] = pi
            old_agents_actions[agent_idx] = actions[agent_idx]

        threads = []
        for agent_idx, agent in enumerate(self.agents):
            thread = Thread(target=_get, args=(agent_idx, agent), daemon=True)
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        all_agents_new_actions = [all_agents_new_actions[i] for i in range(self.n_agents)]
        all_agents_new_mu_actions = [all_agents_new_mu_actions[i] for i in range(self.n_agents)]
        old_agents_actions = [old_agents_actions[i] for i in range(self.n_agents)]

        new_actions = T.cat([acts for acts in all_agents_new_actions], dim=1)
        mu = T.cat([acts for acts in all_agents_new_mu_actions], dim=1).clone().detach()
        old_actions = T.cat([acts for acts in old_agents_actions],dim=1)

        def _learn(agent_idx, agent):
            critic_value_ = agent.target_critic.forward(states_, new_actions).flatten()
            critic_value_[dones[:,0]] = 0.0
            critic_value = agent.critic.forward(states, old_actions).flatten()

            target = rewards[:,agent_idx] + agent.gamma*critic_value_.clone().detach()
            critic_loss = F.mse_loss(target.to(T.float32), critic_value.to(T.float32))
            agent.critic.optimizer.zero_grad()
            critic_loss.backward(retain_graph=True)
            agent.critic.optimizer.step()

            actor_loss = agent.critic.forward(states, mu).flatten()
            actor_loss = -T.mean(actor_loss)
            agent.actor.optimizer.zero_grad()
            actor_loss.backward(retain_graph=True)
            agent.actor.optimizer.step()

            agent.update_network_parameters()
            
        threads = []
        for agent_idx, agent in enumerate(self.agents):
            thread = Thread(target=_learn, args=(agent_idx, agent), daemon=True)
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()

import logging

import numpy as np
import torch

import agents.server as agents
import model
import utils
import flask
import threading
from info.agent import AgentInfo
from info.game import GameInfo

# env_small: 9x9, env_regular: 15x15
from env import env_small as game


BOARD_SIZE = game.Return_BoardParams()[0]

N_BLOCKS_PLAYER = 10
N_BLOCKS_ENEMY = 10

IN_PLANES_PLAYER = 5  # history * 2 + 1
IN_PLANES_ENEMY = 5

OUT_PLANES_PLAYER = 128
OUT_PLANES_ENEMY = 128

N_MCTS = 400
N_MATCH = 12

use_cuda = torch.cuda.is_available()
device = torch.device('cuda' if use_cuda else 'cpu')

# WebAPI
app = flask.Flask(__name__)
log = logging.getLogger('werkzeug')
log.disabled = True
# app.logger.disabled = True

gi = GameInfo(BOARD_SIZE)
player_agent_info = AgentInfo(BOARD_SIZE)
enemy_agent_info = AgentInfo(BOARD_SIZE)

# =========================== input model path ======================== #
#   'human': human play   'random': random     None: raw model MCTS     #
#   'puct': PUCT MCTS     'uct': UCT MCTS     'web': human web player   #
# ===================================================================== #

player_model_path = './data/180825_900_15827_step_model.pickle'
enemy_model_path = './data/180818_900_17841_step_model.pickle'

# ===================================================================== #


class Evaluator(object):
    def __init__(self, model_path_a, model_path_b):
        if model_path_a == 'random':
            print('load player model:', model_path_a)
            self.player = agents.RandomAgent(BOARD_SIZE)
        elif model_path_a == 'puct':
            print('load player model:', model_path_a)
            self.player = agents.PUCTAgent(BOARD_SIZE, N_MCTS)
        elif model_path_a == 'uct':
            print('load player model:', model_path_a)
            self.player = agents.UCTAgent(BOARD_SIZE, N_MCTS)
        elif model_path_a == 'human':
            print('load player model:', model_path_a)
            self.player = agents.HumanAgent(BOARD_SIZE)
        elif model_path_a == 'web':
            print('load player model:', model_path_a)
            self.player = agents.WebAgent(BOARD_SIZE)
        elif model_path_a:
            print('load player model:', model_path_a)
            self.player = agents.ZeroAgent(BOARD_SIZE,
                                           N_MCTS,
                                           IN_PLANES_PLAYER,
                                           noise=False)
            self.player.model = model.PVNetJ(N_BLOCKS_PLAYER,
                                             IN_PLANES_PLAYER,
                                             OUT_PLANES_PLAYER,
                                             BOARD_SIZE).to(device)
            state_a = self.player.model.state_dict()
            my_state_a = torch.load(
                model_path_a, map_location='cuda:0' if use_cuda else 'cpu')
            for k, v in my_state_a.items():
                if k in state_a:
                    state_a[k] = v
            self.player.model.load_state_dict(state_a)
        else:
            print('load player model:', model_path_a)
            self.player = agents.ZeroAgent(BOARD_SIZE,
                                           N_MCTS,
                                           IN_PLANES_PLAYER,
                                           noise=False)
            self.player.model = model.PVNetJ(N_BLOCKS_PLAYER,
                                             IN_PLANES_PLAYER,
                                             OUT_PLANES_PLAYER,
                                             BOARD_SIZE).to(device)
        if model_path_b == 'random':
            print('load enemy model:', model_path_b)
            self.enemy = agents.RandomAgent(BOARD_SIZE)
        elif model_path_b == 'puct':
            print('load enemy model:', model_path_b)
            self.enemy = agents.PUCTAgent(BOARD_SIZE, N_MCTS)
        elif model_path_b == 'uct':
            print('load enemy model:', model_path_b)
            self.enemy = agents.UCTAgent(BOARD_SIZE, N_MCTS)
        elif model_path_b == 'human':
            print('load enemy model:', model_path_b)
            self.enemy = agents.HumanAgent(BOARD_SIZE)
        elif model_path_b == 'web':
            print('load enemy model:', model_path_b)
            self.enemy = agents.WebAgent(BOARD_SIZE)
        elif model_path_b:
            print('load enemy model:', model_path_b)
            self.enemy = agents.ZeroAgent(BOARD_SIZE,
                                          N_MCTS,
                                          IN_PLANES_ENEMY,
                                          noise=False)
            self.enemy.model = model.PVNet(N_BLOCKS_ENEMY,
                                           IN_PLANES_ENEMY,
                                           OUT_PLANES_ENEMY,
                                           BOARD_SIZE).to(device)
            state_b = self.enemy.model.state_dict()
            my_state_b = torch.load(
                model_path_b, map_location='cuda:0' if use_cuda else 'cpu')
            for k, v in my_state_b.items():
                if k in state_b:
                    state_b[k] = v
            self.enemy.model.load_state_dict(state_b)
        else:
            print('load enemy model:', model_path_b)
            self.enemy = agents.ZeroAgent(BOARD_SIZE,
                                          N_MCTS,
                                          IN_PLANES_ENEMY,
                                          noise=False)
            self.enemy.model = model.PVNetJ(N_BLOCKS_ENEMY,
                                            IN_PLANES_ENEMY,
                                            OUT_PLANES_ENEMY,
                                            BOARD_SIZE).to(device)
        self.player_pi = None
        self.enemy_pi = None
        self.player_visit = None
        self.enemy_visit = None

        self.player_monitor = self.player
        self.enemy_monitor = self.enemy

    def get_action(self, root_id, board, turn, enemy_turn):

        if turn != enemy_turn:
            pi = self.player.get_pi(root_id, board, turn, tau=0.01)
            self.player_pi = pi
            self.player_visit = self.player.get_visit()
            action, action_index = utils.argmax_onehot(pi)
        else:
            pi = self.enemy.get_pi(root_id, board, turn, tau=0.01)
            self.enemy_pi = pi
            self.enemy_visit = self.enemy.get_visit()
            action, action_index = utils.argmax_onehot(pi)

        return action, action_index

    def get_pv(self, root_id, turn, enemy_turn):

        if turn != enemy_turn:
            if player_model_path != 'web':
                p, v = self.player_monitor.get_pv(root_id)
            else:
                p, v = self.enemy_monitor.get_pv(root_id)
        else:
            if enemy_model_path != 'web':
                p, v = self.enemy_monitor.get_pv(root_id)
            else:
                p, v = self.player_monitor.get_pv(root_id)

        return p, v

    def reset(self):
        self.player.reset()
        self.enemy.reset()

    # WebAPI
    def put_action(self, action_idx, turn, enemy_turn):

        if turn != enemy_turn:
            if type(self.player) is agents.WebAgent:
                self.player.put_action(action_idx)
        else:
            if type(self.enemy) is agents.WebAgent:
                self.enemy.put_action(action_idx)

    def get_player_message(self):

        if self.player is None:
            return ''

        return 'Player : ' + self.player.get_message()

    def get_enemy_message(self):

        if self.enemy is None:
            return ''

        return 'Enemy : ' + self.enemy.get_message()

    def get_player_visit(self):

        if self.player_visit is None:
            return None

        return self.player_visit

    def get_enemy_visit(self):

        if self.enemy_visit is None:
            return None

        return self.enemy_visit


def elo(player_elo, enemy_elo, p_winscore, e_winscore):
    elo_diff = enemy_elo - player_elo
    ex_pw = 1 / (1 + 10**(elo_diff / 400))
    ex_ew = 1 / (1 + 10**(-elo_diff / 400))
    player_elo += 32 * (p_winscore - ex_pw)
    enemy_elo += 32 * (e_winscore - ex_ew)

    return player_elo, enemy_elo


evaluator = Evaluator(player_model_path, enemy_model_path)


def main():
    print('cuda:', use_cuda)

    # g_evaluator = evaluator

    env = game.GameState('text')
    result = {'Player': 0, 'Enemy': 0, 'Draw': 0}
    turn = 0
    enemy_turn = 1
    gi.enemy_turn = enemy_turn
    player_elo = 1500
    enemy_elo = 1500

    print('Player ELO: {:.0f}, Enemy ELO: {:.0f}'.format(
        player_elo, enemy_elo))

    # i = 0

    for i in range(N_MATCH):
        board = np.zeros([BOARD_SIZE, BOARD_SIZE])
        root_id = (0,)
        win_index = 0
        action_index = None

        if i % 2 == 0:
            print('Player Color: Black')
        else:
            print('Player Color: White')

        while win_index == 0:
            utils.render_str(board, BOARD_SIZE, action_index)
            action, action_index = evaluator.get_action(
                root_id, board, turn, enemy_turn)

            p, v = evaluator.get_pv(root_id, turn, enemy_turn)

            if turn != enemy_turn:
                # player turn
                root_id = evaluator.player.root_id + (action_index,)
            else:
                # enemy turn
                root_id = evaluator.enemy.root_id + (action_index,)

            board, check_valid_pos, win_index, turn, _ = env.step(action)

            # WebAPI
            gi.game_board = board
            gi.action_index = int(action_index)
            gi.win_index = win_index
            gi.curr_turn = turn

            move = np.count_nonzero(board)

            if evaluator.get_player_visit() is not None:
                player_agent_info.visit = evaluator.get_player_visit()

            if evaluator.get_enemy_visit() is not None:
                enemy_agent_info.visit = evaluator.get_enemy_visit()

            if turn == enemy_turn:
                evaluator.enemy.del_parents(root_id)
                player_agent_info.add_value(move, v)
                player_agent_info.p = p

            else:
                evaluator.player.del_parents(root_id)
                enemy_agent_info.add_value(move, v)
                enemy_agent_info.p = p

            # used for debugging
            if not check_valid_pos:
                raise ValueError('no legal move!')

            if win_index != 0:
                player_agent_info.clear_values()
                enemy_agent_info.clear_values()
                if turn == enemy_turn:
                    if win_index == 3:
                        result['Draw'] += 1
                        print('\nDraw!')
                        player_elo, enemy_elo = elo(
                            player_elo, enemy_elo, 0.5, 0.5)
                    else:
                        result['Player'] += 1
                        print('\nPlayer Win!')
                        player_elo, enemy_elo = elo(
                            player_elo, enemy_elo, 1, 0)
                else:
                    if win_index == 3:
                        result['Draw'] += 1
                        print('\nDraw!')
                        player_elo, enemy_elo = elo(
                            player_elo, enemy_elo, 0.5, 0.5)
                    else:
                        result['Enemy'] += 1
                        print('\nEnemy Win!')
                        player_elo, enemy_elo = elo(
                            player_elo, enemy_elo, 0, 1)

                utils.render_str(board, BOARD_SIZE, action_index)
                # Change turn
                enemy_turn = abs(enemy_turn - 1)
                gi.enemy_turn = enemy_turn
                turn = 0
                pw, ew, dr = result['Player'], result['Enemy'], result['Draw']
                winrate = (pw + 0.5 * dr) / (pw + ew + dr) * 100
                print('')
                print('=' * 20, " {}  Game End  ".format(i + 1), '=' * 20)
                print('Player Win: {}'
                      '  Enemy Win: {}'
                      '  Draw: {}'
                      '  Winrate: {:.2f}%'.format(
                          pw, ew, dr, winrate))
                print('Player ELO: {:.0f}, Enemy ELO: {:.0f}'.format(
                    player_elo, enemy_elo))
                evaluator.reset()
        # i = i + 1


# WebAPI
@app.route('/')
def home():
    return flask.render_template('index.html')


@app.route('/dashboard')
def dashboard():
    return flask.render_template('dashboard.html')


@app.route('/test')
def test():
    return flask.render_template('test.html')


@app.route('/periodic_status')
def periodic_status():

    data = {"success": False}

    data["game_board_size"] = gi.game_board.shape[0]
    data["game_board_values"] = gi.game_board.reshape(
        gi.game_board.size).astype(int).tolist()
    data["game_board_message"] = gi.message
    data["action_index"] = gi.action_index
    data["win_index"] = gi.win_index
    data["curr_turn"] = gi.curr_turn

    data["player_agent_p_size"] = player_agent_info.p_size
    data["player_agent_p_values"] = player_agent_info.p.reshape(
        player_agent_info.p_size).astype(float).tolist()
    data["player_agent_visit_size"] = player_agent_info.visit_size
    data["player_agent_visit_values"] = player_agent_info.visit.reshape(
        player_agent_info.visit_size).astype(float).tolist()

    data["enemy_agent_p_size"] = enemy_agent_info.p_size
    data["enemy_agent_p_values"] = enemy_agent_info.p.reshape(
        enemy_agent_info.p_size).astype(float).tolist()
    data["enemy_agent_visit_size"] = enemy_agent_info.visit_size
    data["enemy_agent_visit_values"] = enemy_agent_info.visit.reshape(
        enemy_agent_info.visit_size).astype(float).tolist()

    data["player_agent_moves"] = player_agent_info.moves
    data["player_agent_values"] = player_agent_info.values
    data["enemy_agent_moves"] = enemy_agent_info.moves
    data["enemy_agent_values"] = enemy_agent_info.values

    data["success"] = True

    return flask.jsonify(data)


@app.route('/prompt_status')
def prompt_status():
    data = {"success": False}

    data["player_message"] = evaluator.get_player_message()
    data["enemy_message"] = evaluator.get_enemy_message()
    data["success"] = True

    return flask.jsonify(data)


if __name__ == '__main__':
    np.set_printoptions(suppress=True)
    np.random.seed(0)
    torch.manual_seed(0)
    if use_cuda:
        torch.cuda.manual_seed_all(0)

    # WebAPI
    print("Activate WebAPI...")
    app_th = threading.Thread(
        target=app.run,
        kwargs={"host": "0.0.0.0", "port": 5000}
    )
    app_th.start()
    main()

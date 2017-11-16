from collections import defaultdict, deque
from enum import IntEnum
from functools import partial, wraps
from itertools import combinations, product
from operator import attrgetter
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

from model.ActionType import ActionType
from model.Game import Game
from model.Move import Move
from model.Player import Player
from model.Vehicle import Vehicle
from model.VehicleType import VehicleType
from model.VehicleUpdate import VehicleUpdate
from model.World import World


Cluster = NamedTuple('Cluster', [('vehicles', List[Vehicle]), ('size', float)])


class Group(IntEnum):
    """
    Group IDs.
    """
    ALL = 1
    ARRV = 2
    FIGHTER = 3
    HELICOPTER = 4
    IFV = 5
    TANK = 6
    NUCLEAR_STRIKE_VEHICLE_BASE = 10


class MyStrategy:
    def __init__(self):
        self.action_queue = deque()

        unit_tracker = UnitTracker(self)

        self.trackers = (unit_tracker,)
        self.decision_makers = (
            InitialSetupDecisionMaker(self),
            NuclearStrikeDecisionMaker(self, unit_tracker),
        )

        self.me = None  # type: Player
        self.world = None  # type: World
        self.game = None  # type: Game
        self.move_ = None  # type: Move
        self.opponent_player_id = None  # type: int

    def move(self, me: Player, world: World, game: Game, move: Move):
        """
        Entry point.
        """
        self.me = me
        self.world = world
        self.game = game
        self.move_ = move
        self.opponent_player_id = world.get_opponent_player().id

        for tracker in self.trackers:
            tracker.move()
        if self.action_queue:
            self.process_action_queue()
        else:
            self.make_decisions()

    def schedule_action(self, action: Callable[[], None]):
        """
        Put the action to the queue.
        """
        self.action_queue.append(action)

    def process_action_queue(self):
        """
        Process the next action if possible.
        """
        if self.me.remaining_action_cooldown_ticks == 0:
            self.action_queue.popleft()()

    def log_message(self, message: str, *args, **kwargs):
        print('[{}] {}'.format(self.world.tick_index, message.format(*args, **kwargs)))

    def make_decisions(self):
        """
        This is where strategy decisions are made.
        """
        for decision_maker in self.decision_makers:
            if decision_maker.move():
                self.log_message('{} made its decision!', decision_maker)
                break

    def select_all(self, vehicle_type: Optional[VehicleType] = None):
        def wrapper():
            self.log_message('select all {}', vehicle_type)
            self.move_.action = ActionType.CLEAR_AND_SELECT
            self.move_.left = 0.0
            self.move_.top = 0.0
            self.move_.right = self.game.world_width
            self.move_.bottom = self.game.world_height
            if vehicle_type is not None:
                self.move_.vehicle_type = vehicle_type
        self.schedule_action(wrapper)

    def assign_group(self, group: Group):
        def wrapper():
            self.log_message('assign group {}', group.name)
            self.move_.action = ActionType.ASSIGN
            self.move_.group = group
        self.schedule_action(wrapper)

    def select_group(self, group: Group):
        def wrapper():
            self.log_message('select group {}', group.name)
            self.move_.action = ActionType.CLEAR_AND_SELECT
            self.move_.group = group
        self.schedule_action(wrapper)

    def scale(self, x: float, y: float, factor: float):
        def wrapper():
            self.log_message('scale around ({}, {}) by {}', x, y, factor)
            self.move_.action = ActionType.SCALE
            self.move_.x = x
            self.move_.y = y
            self.move_.factor = factor
        self.schedule_action(wrapper)

    def go(self, offset_x: float, offset_y: float, max_speed: Optional[float] = None):
        def wrapper():
            self.log_message('move by ({}, {}) speed {}', offset_x, offset_y, max_speed)
            self.move_.action = ActionType.MOVE
            self.move_.x = offset_x
            self.move_.y = offset_y
            if max_speed is not None:
                self.move_.max_speed = max_speed
        self.schedule_action(wrapper)


class InitialSetupDecisionMaker:
    def __init__(self, strategy: MyStrategy):
        self.strategy = strategy

    def move(self) -> bool:
        if self.strategy.world.tick_index == 0:
            self.strategy.select_all()
            self.strategy.assign_group(Group.ALL)
            self.strategy.select_all(VehicleType.ARRV)
            self.strategy.assign_group(Group.ARRV)
            self.strategy.select_all(VehicleType.FIGHTER)
            self.strategy.assign_group(Group.FIGHTER)
            self.strategy.select_all(VehicleType.HELICOPTER)
            self.strategy.assign_group(Group.HELICOPTER)
            self.strategy.select_all(VehicleType.IFV)
            self.strategy.assign_group(Group.IFV)
            self.strategy.select_all(VehicleType.TANK)
            self.strategy.assign_group(Group.TANK)
            return True
        else:
            return False

    def __str__(self):
        return self.__class__.__name__


class NuclearStrikeDecisionMaker:
    def __init__(self, strategy: MyStrategy, unit_tracker: 'UnitTracker'):
        self.strategy = strategy
        self.unit_tracker = unit_tracker

    def move(self) -> bool:
        pass

    def __str__(self):
        return self.__class__.__name__


class UnitTracker:
    CELL_COUNT = 64
    CELL_SIZE = 1024 / CELL_COUNT
    CELL_SIZE_SQUARED = CELL_SIZE * CELL_SIZE
    SCAN_RANGE = range(-1, 2)

    def __init__(self, strategy: MyStrategy):
        self.strategy = strategy
        self.vehicles = {}  # type: Dict[int, Vehicle]
        self.cells = defaultdict(dict)  # type: Dict[Tuple[int, int], Dict[int, Vehicle]]
        self._clusters = None  # type: List[Cluster]

    @property
    def clusters(self) -> List[Cluster]:
        if self._clusters is None:
            self._clusters = sorted(self.clusterize_opponent_vehicles(), key=attrgetter('size'), reverse=True)
            self.strategy.log_message('clusters: {}', [(len(vehicles), size) for vehicles, size in self.clusters])
        return self._clusters

    def move(self):
        self.reset()
        self.add_new_vehicles()
        self.update_vehicles()

    def reset(self):
        """
        Reset all fields that are not refreshed on each tick.
        """
        self._clusters = None

    def add_new_vehicles(self):
        """
        Add new vehicles on each tick.
        """
        for vehicle in self.strategy.world.new_vehicles:  # type: Vehicle
            self.vehicles[vehicle.id] = vehicle
            if vehicle.player_id == self.strategy.opponent_player_id:
                self.get_cell(vehicle)[vehicle.id] = vehicle

    def update_vehicles(self):
        """
        Update vehicles on each tick.
        """
        for update in self.strategy.world.vehicle_updates:  # type: VehicleUpdate
            vehicle = self.vehicles[update.id]
            is_opponent = vehicle.player_id == self.strategy.opponent_player_id
            if is_opponent:
                # Pop out from the old cell.
                self.get_cell(vehicle).pop(vehicle.id, None)
            if update.durability != 0:
                vehicle.x = update.x
                vehicle.y = update.y
                vehicle.durability = update.durability
                vehicle.groups = update.groups
                vehicle.selected = update.selected
                vehicle.remaining_attack_cooldown_ticks = update.remaining_attack_cooldown_ticks
                if is_opponent:
                    # Put to the right cell.
                    self.get_cell(vehicle)[vehicle.id] = vehicle
            else:
                self.vehicles.pop(update.id, None)

    def clusterize_opponent_vehicles(self) -> Iterable[Cluster]:
        """
        Split opponent vehicles into clusters.
        """
        cells = {cell for cell, vehicles in self.cells.items() if vehicles}
        while cells:
            vehicles = list(self.bfs(cells, *cells.pop()))
            if vehicles:
                yield Cluster(vehicles, self.get_cluster_size(vehicles))

    def bfs(self, cells: Set[Tuple[int, int]], i: int, j: int) -> Iterable[Vehicle]:
        """
        Run BFS from the specified cell.
        """
        queue = deque([(i, j)])
        while queue:
            i, j = queue.popleft()
            yield from self.cells[i, j].values()
            for delta_i, delta_j in product(self.SCAN_RANGE, self.SCAN_RANGE):
                next_i, next_j = i + delta_i, j + delta_j
                if (next_i, next_j) in cells and self.get_minimum_squared_distance(i, j, next_i, next_j) < self.CELL_SIZE_SQUARED:
                    cells.remove((next_i, next_j))
                    queue.append((next_i, next_j))

    def get_minimum_squared_distance(self, i1: int, j1: int, i2: int, j2: int) -> float:
        """
        Get minimum squared distance between vehicles in the clusters.
        """
        return min(
            vehicle_1.get_squared_distance_to_unit(vehicle_2)
            for vehicle_1, vehicle_2 in product(self.cells[i1, j1].values(), self.cells[i2, j2].values())
        )

    @staticmethod
    def get_cluster_size(vehicles: Iterable[Vehicle]) -> float:
        """
        Get cluster size as the maximum distance between vehicles in the cluster.
        """
        return max((
            vehicle_1.get_distance_to_unit(vehicle_2)
            for vehicle_1, vehicle_2 in combinations(vehicles, 2)
        ), default=0.0)

    def get_cell(self, vehicle: Vehicle) -> Dict[int, Vehicle]:
        return self.cells[int(vehicle.x // UnitTracker.CELL_SIZE), int(vehicle.y // UnitTracker.CELL_SIZE)]

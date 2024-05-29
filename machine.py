#!/usr/bin/python3
import logging
import sys


def mod_in_ring(number, n):
    return (number % n + n) % n


def crop_int_to_int32(num):
    max_int32 = 2**31 - 1
    cropped_num = num & 0xFFFFFFFF
    if cropped_num & 0x80000000:
        return -(0x100000000 - cropped_num) if cropped_num > max_int32 else cropped_num
    else:
        return cropped_num


def crop_int_to_int16(num):
    max_int32 = 2**15 - 1
    cropped_num = num & 0xFFFF
    if cropped_num & 0x8000:
        return -(0x10000 - cropped_num) if cropped_num > max_int32 else cropped_num
    else:
        return cropped_num


def crop_int_to_uint16(num):
    max_uint16 = 2**16 - 1
    min_uint16 = 0
    cropped_num = num & 0xFFFF
    return min(max_uint16, max(min_uint16, cropped_num))


class MemoryManager:
    def __init__(self, size):
        self.memory = [0] * size
        self.size = size

    def setmem(self, address, value):
        self.memory[mod_in_ring(address, self.size)] = value

    def getmem(self, address):
        return self.memory[mod_in_ring(address, self.size)]


class magic_numbers:
    MUX_A_ALU = 0
    MUX_A_INP = 1

    MUX_L_AC = 0
    MUX_L_AR = 1
    MUX_L_0 = 2

    MUX_R_DR = 0
    MUX_R_0 = 1
    MUX_R_SP = 2
    MUX_R_PR = 3

    MUX_S_INC = 0
    MUX_S_DEC = 1


class DataPath:
    """

     ! Каждый MUX имеет селектор, но ради упрощения схемы они не отображены

                         ^                  |
                         |32bit             |PR
                         |                  |16bit
     +---+---------------+                  |
     |   |               |                  |
     |   |   |           |                  |
     |   |   | inp    _C___S_               |
     |   v   v       /       \  alu_op      |
     | +-------+    /   ___   \<------      |
     | | MUX_A |   /   /   \   \            |
     | +-------+  +---/     \---+         sign
     |    |         ^         ^         extension
     |    |latch_ac |         |             |
     |    v         |         |             |32bit
     | +----+   +-------+ +-------+         |
     | | AC |-->| MUX_L | | MUX_R |<--------+
     | +----+   +-------+ +-------+
     |     |     ^     ^   ^  ^  ^
     | out |     |     |   |  |  |
     |     v     |    (0)  | (0) +---------------+----+
     |           |         |     |               |    |
     |           |         |     |             (+1)  (-1)
     |           |         |     |               |    |
     |           |         |     |               v    v
     |latch_ar+----+    +----+ +----+ latch_sp +-------+
     +------->| AR |    | DR | | SP |<---------| MUX_S |
     |        +----+    +----+ +----+          +-------+
     |           |         ^
     |           |         |           |    |
     |value      |address  |data_out   |wr  |oe
     v           v         |           v    v
    +---------------------------------------------------+
    |  data                                             |
    |  memory                                           |
    +---------------------------------------------------+

    - data_memory -- однопортовая, поэтому либо читаем, либо пишем.

    - input/output -- токенизированная логика ввода-вывода. Не детализируется в
      рамках модели.

    - input -- чтение может вызвать остановку процесса моделирования, если буфер
      входных значений закончился.

    Реализованные методы соответствуют сигналам защёлкивания значений:

    - `signal_latch_ip` -- защёлкивание адреса следующей выполняемой команды;
    - `signal_latch_ac` -- защёлкивание аккумулятора;
    - `signal_latch_ar` -- защёлкивание адреса в памяти;
    - `signal_latch_sp` -- защёлкивание адреса вершины стека;
    - `signal_oe` -- чтение из память;
    - `signal_wr` -- запись в память;
    - `signal_out` -- вывод в порт.

    Сигнал "исполняется" за один такт. Корректность использования сигналов --
    задача `ControlUnit`.
    """

    memory_manager = None
    rAC = None
    rAR = None
    rSP = None
    rDR = None
    alu_flags = None
    input_buffer = None
    output_buffer = None

    def __init__(self, memory_manager, input_buffer):
        self.memory_manager = memory_manager
        self.rAC = 0
        self.rAR = 0
        self.rSP = 0
        self.rDR = 0
        self.alu_flags = {"Z": True, "S": False}
        self.input_buffer = input_buffer
        self.output_buffer = []

    def signal_latch_ip(self, sel_l, sel_r, alu_op):
        self.rAR = self.alu(sel_l, sel_r, alu_op)

    def signal_latch_ac(self, sel_l, sel_r, alu_op, sel_a=magic_numbers.MUX_A_ALU):
        if sel_a == magic_numbers.MUX_A_INP:
            if len(self.input_buffer) == 0:
                raise EOFError()
            self.rAC = self.input_buffer.pop(0)
        else:
            self.rAC = self.alu(sel_l, sel_r, alu_op)

    def signal_latch_ar(self, sel_l, sel_r, alu_op):
        self.rAR = self.alu(sel_l, sel_r, alu_op)

    def signal_latch_sp(self, sel_s):
        if sel_s == magic_numbers.MUX_S_INC:
            self.rSP += 1
        else:
            self.rSP -= 1

    def signal_oe(self):
        self.rDR = self.memory_manager.getmem(self.rAR)

    def signal_wr(self, sel_l, sel_r, alu_op):
        self.memory_manager.setmem(self.rAR, self.alu(sel_l, sel_r, alu_op))

    def signal_malloc(self, sel_l, sel_r, alu_op):
        self.memory_manager.malloc(self.alu(sel_l, sel_r, alu_op))

    def signal_out(self):
        symbol = chr(self.rAC)
        self.output_buffer.append(symbol)

    def zero(self):
        return self.alu_flags["Z"]

    def sign(self):
        return self.alu_flags["S"]

    def alu(self, sel_l, sel_r, alu_op):
        left_value = 0
        right_value = 0
        if sel_l == magic_numbers.MUX_L_AC:
            left_value = self.rAC
        elif sel_l == magic_numbers.MUX_L_AR:
            left_value = self.rAR

        if sel_r == magic_numbers.MUX_R_DR:
            right_value = self.rDR
        elif sel_r == magic_numbers.MUX_R_SP:
            right_value = self.rSP
        elif sel_r == magic_numbers.MUX_R_PR:
            right_value = alu_op["PR"]

        out_value = 0
        if alu_op["op"] == "ADD":
            out_value = left_value + right_value
        elif alu_op["op"] == "SUB":
            out_value = left_value - right_value
        elif alu_op["op"] == "MUL":
            out_value = left_value * right_value
        elif alu_op["op"] == "DIV":
            assert right_value != 0, "/0"
            out_value = left_value // right_value
        elif alu_op["op"] == "MOD":
            assert right_value != 0, "%0"
            out_value = left_value % right_value
        elif alu_op["op"] == "CMP":
            out_value = left_value
            self.alu_flags["Z"] = (left_value - right_value) == 0
            self.alu_flags["S"] = (left_value - right_value) < 0
            return crop_int_to_int32(out_value)

        if "set_flag" in alu_op:
            self.alu_flags["Z"] = out_value == 0
            self.alu_flags["S"] = out_value < 0

        return crop_int_to_int32(out_value)


# CPU registers:
# AC - 32-bit ACCUMULATOR. General purpose register
# SP - 32-bit STACK POINTER
# CPU 2 flags: Z - zero, S - sign

# MEM(32 bit address) = 32bit value by address
#             00       01         10           11
# F(ARG) ::= ARG || MEM(ARG) || SP+ARG || MEM(SP+ARG)

"""
Инструкции:
Операции с памятью:
- `LD` - загружает в аккумулятор указанное значение
- `ST` - сохраняет значение из аккумулятора в указанную ячейку памяти

Арифметические операции (результат записывается в аккумулятор):
- `ADD` - произвести сложение аккумулятора с указанным значением
- `SUB` - произвести вычитание аккумулятора с указанным значением
- `MUL` - произвести умножение аккумулятора с указанным значением
- `DIV` - произвести деление аккумулятора с указанным значением
- `MOD` - вычисление остатка от деления аккумулятора с указанным значением
- `CMP` - установить флаги по вычитанию указанного значения из аккумулятора

Инструкции перехода:
- `JMP` - безусловный переход на указанное место
- `JE` - переход на указанное место, если флаг 'Z' равен 1. Переход, если равно
- `JNE` - переход на указанное место, если флаг 'Z' равен 0. Переход, если не равно
- `JGE` - переход на указанное место, если флаг 'N' равен 0. Переход, если больше или равно

Инструкции подпрограмм:
- `CALL` - вызов подпрограммы
- `RET` - возврат из функции

Операции со стеком:
- `PUSH` - положить на вершину стека значение аккумулятора
- `POP` - записать в аккумулятор значение из вершины стека

Операции ввода-вывода:
- `IN` - прочитать байт в аккумулятор
- `OUT` - вывести младший байт

Остальные:
- `NOP`
- `HALT` - остановить процессор
"""


class ControlUnit:
    """
    Блок управления процессора. Выполняет декодирование инструкций и
    управляет состоянием модели процессора, включая обработку данных (DataPath).

    Согласно варианту, любая инструкция может быть закодирована в одно слово.
    Следовательно, индекс памяти команд эквивалентен номеру инструкции.


      +----------------------- +1 -----------+             +---------------------------+
      |                                      |             |                           |
      |                                      v             |                           |
      |      +---------+             +-----------------+   |    +-----------------+    |
      +----->|         |    latch_ip |   instruction   |   |    |     program     |    |
             |   MUX   |------------>|     pointer     |---+--->|      memory     |    |
      +----->|         |             |                 |        |                 |    |
      |      +---------+             +-----------------+        +-----------------+    |
      |             ^                                                    |             |
      |             |                                                    +---------+   |
      |             |                                                    |         |   |
      |             |                                                    |         v   v
      |             |                                                    |      +---------+
      |             |                                                    |      |   MUX   |
      |             |                                                    |      +---------+
      |             |                                                    |           |
      |             |                                                    |           v
      |             |                                                    |   +----------------+
      |             |                                                    |   | trim_low_16bit |
      |             |                                                    |   +----------------+
      |             |                                                    V           |
      |             |                                 +------------------------+     |
      |             +---------------------------------|       instruction      |     |
      |                                               |         decoder        |     |
      |                                               +------------------------+     |
      |                                                     ^            |           |
      |                                                     |            |           |
      |                                                   flags       signals        |
      |                                                     |            |           |
      |                                                     |            V           |
      |                                                  +-----------------+         |
      |                                                  |     DataPath    |<--------+
      |                                                  +-----------------+
      |                                                           |
      +-------------------------- data ---------------------------+

    """

    data_path = None
    programm = None
    IP = None
    _tick = None

    def __init__(self, programm, data_path):
        self.data_path = data_path
        self.programm = programm
        self.IP = 0
        self._tick = 0

    def tick(self, cnt=1):
        self._tick += cnt

    def current_tick(self):
        return self._tick

    def execute_instruction(self, instr, data):
        if instr == "NOP":  # NOP
            return
        elif instr == "HALT":  # HALT
            raise "STOP"
        elif instr == "LD":
            if data["F"] == 0:  # LD 5
                self.data_path.signal_latch_ac(
                    magic_numbers.MUX_L_0,
                    magic_numbers.MUX_R_PR,
                    {"op": "ADD", "PR": data["V"]},
                )
                self.tick()
            elif data["F"] == 1:  # LD [5]
                self.data_path.signal_latch_ar(
                    magic_numbers.MUX_L_0,
                    magic_numbers.MUX_R_PR,
                    {"op": "ADD", "PR": data["V"]},
                )
                self.tick(3)
                self.data_path.signal_oe()
                self.data_path.signal_latch_ac(magic_numbers.MUX_L_0, magic_numbers.MUX_R_DR, {"op": "ADD"})
            elif data["F"] == 2:  # LD [SP+5]
                self.data_path.signal_latch_ar(
                    magic_numbers.MUX_L_0,
                    magic_numbers.MUX_R_PR,
                    {"op": "ADD", "PR": data["V"]},
                )
                self.tick(4)
                self.data_path.signal_latch_ar(magic_numbers.MUX_L_AR, magic_numbers.MUX_R_SP, {"op": "ADD"})
                self.data_path.signal_oe()
                self.data_path.signal_latch_ac(magic_numbers.MUX_L_0, magic_numbers.MUX_R_DR, {"op": "ADD"})
            elif data["F"] == 3:  # LD [[SP+5]]
                self.data_path.signal_latch_ar(
                    magic_numbers.MUX_L_0,
                    magic_numbers.MUX_R_PR,
                    {"op": "ADD", "PR": data["V"]},
                )
                self.tick(6)
                self.data_path.signal_latch_ar(magic_numbers.MUX_L_AR, magic_numbers.MUX_R_SP, {"op": "ADD"})
                self.data_path.signal_oe()
                self.data_path.signal_latch_ar(magic_numbers.MUX_L_0, magic_numbers.MUX_R_DR, {"op": "ADD"})
                self.data_path.signal_oe()
                self.data_path.signal_latch_ac(magic_numbers.MUX_L_0, magic_numbers.MUX_R_DR, {"op": "ADD"})
            else:
                raise "E338"
        elif instr == "ST":
            if data["F"] == 0:  # ST 5
                self.data_path.signal_latch_ar(
                    magic_numbers.MUX_L_0,
                    magic_numbers.MUX_R_PR,
                    {"op": "ADD", "PR": data["V"]},
                )
                self.tick(2)
                self.data_path.signal_wr(magic_numbers.MUX_L_AC, magic_numbers.MUX_R_0, {"op": "ADD"})
            elif data["F"] == 1:  # ST [5]
                self.data_path.signal_latch_ar(
                    magic_numbers.MUX_L_0,
                    magic_numbers.MUX_R_PR,
                    {"op": "ADD", "PR": data["V"]},
                )
                self.tick(4)
                self.data_path.signal_oe()
                self.data_path.signal_latch_ar(magic_numbers.MUX_L_0, magic_numbers.MUX_R_DR, {"op": "ADD"})
                self.data_path.signal_wr(magic_numbers.MUX_L_AC, magic_numbers.MUX_R_0, {"op": "ADD"})
            elif data["F"] == 2:  # ST SP+5
                self.data_path.signal_latch_ar(
                    magic_numbers.MUX_L_0,
                    magic_numbers.MUX_R_PR,
                    {"op": "ADD", "PR": data["V"]},
                )
                self.tick(3)
                self.data_path.signal_latch_ar(magic_numbers.MUX_L_AR, magic_numbers.MUX_R_SP, {"op": "ADD"})
                self.data_path.signal_wr(magic_numbers.MUX_L_AC, magic_numbers.MUX_R_0, {"op": "ADD"})
            elif data["F"] == 3:  # ST [SP+5]
                self.data_path.signal_latch_ar(
                    magic_numbers.MUX_L_0,
                    magic_numbers.MUX_R_PR,
                    {"op": "ADD", "PR": data["V"]},
                )
                self.tick(5)
                self.data_path.signal_latch_ar(magic_numbers.MUX_L_AR, magic_numbers.MUX_R_SP, {"op": "ADD"})
                self.data_path.signal_oe()
                self.data_path.signal_latch_ar(magic_numbers.MUX_L_0, magic_numbers.MUX_R_DR, {"op": "ADD"})
                self.data_path.signal_wr(magic_numbers.MUX_L_AC, magic_numbers.MUX_R_0, {"op": "ADD"})
            else:
                raise "E338"
        elif instr in ("ADD", "SUB", "MUL", "DIV", "MOD", "CMP"):
            if data["F"] == 0:  # ADD 5
                self.data_path.signal_latch_ac(
                    magic_numbers.MUX_L_AC,
                    magic_numbers.MUX_R_PR,
                    {"op": instr, "PR": data["V"], "set_flag": True},
                )
                self.tick()
            elif data["F"] == 1:  # LD [5]
                self.data_path.signal_latch_ar(
                    magic_numbers.MUX_L_0,
                    magic_numbers.MUX_R_PR,
                    {"op": "ADD", "PR": data["V"]},
                )
                self.tick(3)
                self.data_path.signal_oe()
                self.data_path.signal_latch_ac(
                    magic_numbers.MUX_L_AC,
                    magic_numbers.MUX_R_DR,
                    {"op": instr, "set_flag": True},
                )
            elif data["F"] == 2:  # ADD SP
                raise "'ADD', 'SUB', 'MUL', 'DIV', 'MOD', 'CMP' with SP not available, only [SP+V]"
            elif data["F"] == 3:  # LD [SP+5]
                self.data_path.signal_latch_ar(
                    magic_numbers.MUX_L_0,
                    magic_numbers.MUX_R_PR,
                    {"op": "ADD", "PR": data["V"]},
                )
                self.tick(4)
                self.data_path.signal_latch_ar(magic_numbers.MUX_L_AR, magic_numbers.MUX_R_SP, {"op": "ADD"})
                self.data_path.signal_oe()
                self.data_path.signal_latch_ac(
                    magic_numbers.MUX_L_AC,
                    magic_numbers.MUX_R_DR,
                    {"op": instr, "set_flag": True},
                )
            else:
                raise "E338"
        elif instr == "JMP":
            self.IP = self.data_path.alu(
                magic_numbers.MUX_L_0,
                magic_numbers.MUX_R_PR,
                {"op": "ADD", "PR": data["V"]},
            )
            self.tick()
        elif instr == "JE":
            self.tick()
            if self.data_path.zero():
                self.IP = self.data_path.alu(
                    magic_numbers.MUX_L_0,
                    magic_numbers.MUX_R_PR,
                    {"op": "ADD", "PR": data["V"]},
                )
        elif instr == "JNE":
            self.tick()
            if not self.data_path.zero():
                self.IP = self.data_path.alu(
                    magic_numbers.MUX_L_0,
                    magic_numbers.MUX_R_PR,
                    {"op": "ADD", "PR": data["V"]},
                )
        elif instr == "JGE":
            self.tick()
            if not self.data_path.sign():
                self.IP = self.data_path.alu(
                    magic_numbers.MUX_L_0,
                    magic_numbers.MUX_R_PR,
                    {"op": "ADD", "PR": data["V"]},
                )
        elif instr == "CALL":
            self.data_path.signal_latch_sp(magic_numbers.MUX_S_DEC)
            self.tick(4)
            self.data_path.signal_latch_ar(magic_numbers.MUX_L_0, magic_numbers.MUX_R_SP, {"op": "ADD"})
            self.data_path.signal_wr(
                magic_numbers.MUX_L_0,
                magic_numbers.MUX_R_PR,
                {"op": "ADD", "PR": self.IP},
            )
            self.IP = self.data_path.alu(
                magic_numbers.MUX_L_0,
                magic_numbers.MUX_R_PR,
                {"op": "ADD", "PR": data["V"]},
            )
        elif instr == "RET":
            self.data_path.signal_latch_ar(magic_numbers.MUX_L_0, magic_numbers.MUX_R_SP, {"op": "ADD"})
            self.tick(4)
            self.data_path.signal_oe()
            self.IP = self.data_path.alu(magic_numbers.MUX_L_0, magic_numbers.MUX_R_DR, {"op": "ADD"})
            self.data_path.signal_latch_sp(magic_numbers.MUX_S_INC)
        elif instr == "PUSH":
            self.data_path.signal_latch_sp(magic_numbers.MUX_S_DEC)
            self.tick(4)
            self.data_path.signal_latch_ar(magic_numbers.MUX_L_0, magic_numbers.MUX_R_SP, {"op": "ADD"})
            self.data_path.signal_wr(magic_numbers.MUX_L_AC, magic_numbers.MUX_R_0, {"op": "ADD"})
            # self.IP = self.data_path.alu(magic_numbers.MUX_L_0, magic_numbers.MUX_R_PR, {"op":"ADD", "PR": data["V"]})
        elif instr == "POP":
            self.data_path.signal_latch_ar(magic_numbers.MUX_L_0, magic_numbers.MUX_R_SP, {"op": "ADD"})
            self.tick(4)
            self.data_path.signal_oe()
            self.data_path.signal_latch_ac(magic_numbers.MUX_L_0, magic_numbers.MUX_R_DR, {"op": "ADD"})
            self.data_path.signal_latch_sp(magic_numbers.MUX_S_INC)
        elif instr == "IN":
            self.data_path.signal_latch_ac(None, None, None, magic_numbers.MUX_A_INP)
            self.tick(1)
        elif instr == "OUT":
            self.data_path.signal_out()

    def decode_value(self, s):
        import re

        if re.search(r"^-?[0-9]+$", s):
            return (0, crop_int_to_int16(int(s)))
        if re.search(r"^\[-?[0-9]+\]$", s):
            return (1, crop_int_to_int16(int(s[1:-1])))
        if re.search(r"^SP[-+][0-9]+$", s):
            return (2, crop_int_to_int16(int(s[2:])))
        if re.search(r"^\[SP[-+][0-9]+\]$", s):
            return (3, crop_int_to_int16(int(s[3:-1])))
        return None

    def decode_and_execute_instruction(self):
        assert 0 <= self.IP and self.IP < len(self.programm), "Unexpected end of the program"
        instr = self.programm[self.IP].copy()
        self.IP += 1
        self.tick()
        if "operand" in instr:
            assert self.decode_value(instr["operand"]) is not None, "Invalid operand"
            instr["F"] = self.decode_value(instr["operand"])[0]
            instr["V"] = self.decode_value(instr["operand"])[1]
        self.execute_instruction(instr["instruction"], instr)

    def __repr__(self):  # TODO S Z
        return "TICK: {:4} ACC: {:6} SP: {:6} IP: {:6} INSTR: {}".format(
            self._tick,
            self.data_path.rAC,
            self.data_path.rSP,
            self.IP,
            self.programm[self.IP],
        )


def simulation(code, input_tokens, data_memory_size, limit):
    mm = MemoryManager(data_memory_size)
    if len(code) > 0 and isinstance(code[0], list):
        for i in range(len(code[0])):
            mm.setmem(i, code[0][i])
        code.pop(0)
    data_path = DataPath(mm, input_tokens)
    control_unit = ControlUnit(code, data_path)
    instr_counter = 0

    logging.debug("%s", control_unit)
    try:
        while instr_counter < limit:
            control_unit.decode_and_execute_instruction()
            instr_counter += 1
            logging.debug("%s", control_unit)
    except EOFError:
        logging.warning("Input buffer is empty!")
    except TypeError:
        pass

    if instr_counter >= limit:
        logging.warning("Limit exceeded!")
    logging.info("output_buffer: %s", repr("".join(data_path.output_buffer)))
    return "".join(data_path.output_buffer), instr_counter, control_unit.current_tick()


def machine(code_file, input_file, debug_file=None):
    def read_code(file_name):
        try:
            import json

            programm = []
            with open(file_name) as f:
                programm = json.load(f)
            return programm
        except:
            print("Reading code error")
            exit(1)

    if debug_file is not None:
        logging.basicConfig(filename=debug_file, filemode="w", level=logging.DEBUG, force=True)
    code = read_code(code_file)
    input_token = []
    with open(input_file, encoding="utf-8") as file:
        input_text = file.read()
        for char in input_text:
            input_token.append(ord(char))
        input_token.append(0)

    output, instr_counter, ticks = simulation(code, input_tokens=input_token, data_memory_size=1000, limit=1500)

    print("".join(output))
    print("instr_counter: ", instr_counter, "ticks:", ticks)


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.DEBUG)
    assert len(sys.argv) == 3, "Wrong arguments: machine.py <code_file> <input_file>"
    _, code_file, input_file = sys.argv
    machine(code_file, input_file)

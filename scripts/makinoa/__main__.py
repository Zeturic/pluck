#!/usr/bin/env python3

import os, sys, argparse, configparser, shutil, subprocess, fnmatch, itertools
import os.path

src = "src"
build = "build"
build_src = os.path.join(build, src)

# switch cwd, if specified by cmd line args
cmdlineargparser = argparse.ArgumentParser(description="Compiles C code and inserts it into a ROM.")
cmdlineargparser.add_argument("root", default=".", nargs="?", help="The root directory of the project to compile and insert. Defaults to the cwd.")

os.chdir(cmdlineargparser.parse_args().root)

# parse config.ini, if it exists
iniparser = configparser.ConfigParser(allow_no_value=True)
iniparser.optionxform = str

try:
    with open("config.ini", "r", encoding="UTF-8") as ini:
        iniparser.read_file(ini, "config.ini")
except FileNotFoundError:
    # no ini file is fine
    pass

free_space = iniparser.get("main", "free-space", fallback="0x08800000")
optimization_level = iniparser.get("main", "optimization-level", fallback="-O2")
reserve = iniparser.get("main", "reserve", fallback="0")
short_calls = set() if "short-calls" not in iniparser else set(iniparser["short-calls"])
defines = {} if "defines" not in iniparser else dict(iniparser["defines"])

try:
    free_space = int(free_space, 16)
except ValueError:
    print(f"Error :: {free_space} is not a hexadecimal integer.")
    sys.exit(1)

try:
    reserve = int(reserve)
except ValueError:
    print(f"Error :: {reserve} is not a decimal integer.")
    sys.exit(1)

if optimization_level not in ("-O", "-O0", "-O1", "-O2", "-O3", "-Ofast", "-Og", "-Os"):
    print(f"Error :: {optimization_level} is not an understood optimization level.")
    sys.exit(1)

# clean build dir
shutil.rmtree(build, ignore_errors=True)
os.mkdir(build)
os.mkdir(build_src)

if "DEVKITARM" in os.environ:
    if os.path.exists(os.environ["DEVKITARM"]):
        CC = os.path.join(os.environ["DEVKITARM"], "bin", "arm-none-eabi-gcc")
        LD = os.path.join(os.environ["DEVKITARM"], "bin", "arm-none-eabi-ld")
    else:
        CC = os.path.join(r"C:/", "devkitPro", "devkitARM", "bin", "arm-none-eabi-gcc")
        LD = os.path.join(r"C:/", "devkitPro", "devkitARM", "bin", "arm-none-eabi-ld")
else:
    CC = shutil.which("arm-none-eabi-gcc")
    LD = shutil.which("arm-none-eabi-ld")

    if CC is None or LD is None:
        print("Error :: Can't find devkitARM.")
        sys.exit(1)

CFLAGS = [
    optimization_level,
    "-Wall",
    "-Wextra",
    "-mthumb",
    "-mno-thumb-interwork",
    "-fno-inline",
    "-fno-builtin",
    "-std=c11",
    "-mcpu=arm7tdmi",
    "-march=armv4t",
    "-mtune=arm7tdmi",
    "-c",
    *itertools.chain.from_iterable(("-D", f"{name}{f'={val}' if val is not None else ''}") for (name,val) in defines.items())
]

LDFLAGS = [
    "--relocatable"
]

long_calls = set()

if os.path.exists(src):
    for srcfile in (path for path in os.listdir(src) if fnmatch.fnmatch(path, "*.c")):
        exit_code = subprocess.run([
            CC,
            *CFLAGS,
            "-mno-long-calls" if srcfile in short_calls else "-mlong-calls",
            os.path.join(src, srcfile),
            "-o",
            os.path.join(build_src, srcfile.replace(".c", ".o"))
        ]).returncode

        if exit_code != 0:
            print("Error :: Compilation failed.")
            sys.exit(exit_code)

        if srcfile not in short_calls:
            long_calls.add(srcfile)

relocatable = os.path.join(build_src, "relocatable.o")

# ensures the file exists
with open(relocatable, "w"): pass

if long_calls:
    exit_code = subprocess.run([
        LD,
        *LDFLAGS,
        *(os.path.join(build_src, srcfile.replace(".c", ".o")) for srcfile in long_calls),
        "-o",
        relocatable
    ]).returncode

    if exit_code != 0:
        print("Error :: Linking failed.")
        sys.exit(exit_code)
        
def round_up_to_4(x):
    if x & 0x3 == 0:
        return x
    else:
        return round_up_to_4(x + 1)

offset_mask = 0x08000000
free_space |= offset_mask

needed_words = round_up_to_4(os.stat(relocatable).st_size + reserve) >> 2
free_space = round_up_to_4(free_space)

def find_needed_words(needed_words, free_space):
    if needed_words == 0:
        return 0
    
    with open("rom.gba", "rb") as rom:
        rom.seek(offset_mask ^ free_space)

        record, start = 0, None

        while record < needed_words:
            val = rom.read(4)

            if val == b"\xff\xff\xff\xff":
                if start is None:
                    if record == 0:
                        record = 1
                    
                    start = rom.tell() - 4
                else:
                    record += 1
            else:
                start = None

    return start ^ offset_mask

shutil.copy("rom.gba", "test.gba")

if "ARMIPS" in os.environ:
    ARMIPS = os.environ["ARMIPS"]
else:
    ARMIPS = shutil.which("armips")
    
    if ARMIPS is None:
        print("Error :: Can't find armips.")
        sys.exit(1)

with open("main.asm", "r", encoding="UTF-8") as instream:
    with open(os.path.join(build, "main.asm"), "w", encoding="UTF-8") as outstream:
        for line in instream:
            print(line, end="", file=outstream)
        print("\n// Beyond this point is autogenerated.", file=outstream)
        print(f".definelabel allocation, {find_needed_words(needed_words, free_space)}", file=outstream)
        for (name, val) in defines.items():
            print(f".definelabel {name}, {val if val is not None else 0}", file=outstream)

exit_code = subprocess.run([
    ARMIPS,
    "-sym",
    "test.sym",
    os.path.join(build, "main.asm"),
    "-equ",
    "allocation_size",
    f"{needed_words << 2}"
]).returncode

if exit_code != 0:
    print("Error :: Assembly failed.")
    sys.exit(exit_code)

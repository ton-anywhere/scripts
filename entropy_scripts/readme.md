### description

small script to generate entropy from biased data rolls and test its randomness

### commands to run

cd entropy_scripts
ruby entropy_scripts/generate_data.rb

### commands to test

sudo apt-get install ent
sudo apt-get install dieharder

ent entropy_scripts/raw_entropy.bin
dieharder -a -f entropy_scripts/raw_entropy.bin

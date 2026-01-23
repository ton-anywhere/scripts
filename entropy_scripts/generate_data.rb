#!/usr/bin/env ruby

require 'openssl'

# Configuration
# 1MB = 1.000.000 bytes =  8.000.000 bits
# 0,7011 entropy for roll
# PROBABILITIES = [0.02, 0.02, 0.02, 0.02, 0.02, 0.90] # Biased distribution: 90% for number 6
PROBABILITIES = [1/6.to_f, 1/6.to_f, 1/6.to_f, 1/6.to_f, 1/6.to_f, 1/6.to_f] # Biased distribution: 90% for number 6
MAX_FACE = 6
# TARGET_ROLLS = 11_409_202
TARGET_ROLLS = 1_409_202

# Generate rolls data
dice_rolls = []
rolls_generated = 0

# Function to generate biased dice rolls
def biased_roll(probabilities)
  rand_num = rand
  accumulated = 0.0

  probabilities.each_with_index do |prob, index|
    accumulated += prob
    return index + 1 if rand_num <= accumulated
  end

  return MAX_FACE # Fallback
end

# Generate data until target size is reached
while dice_rolls.size < TARGET_ROLLS
  roll = biased_roll(PROBABILITIES)
  dice_rolls << roll
  rolls_generated += 1
end

# Convert to binary and write to file
File.open('raw_entropy.bin', 'wb') do |file|
  dice_rolls.each { |value| file.write("%03b" % value) }
end

# Convert to binary and write to file
File.open('raw_entropy_bytes.bin', 'wb') do |file|
  dice_rolls.each { |value| file.write([value].pack('C')) }
end

# Statistics
count = Array.new(MAX_FACE + 1, 0)
dice_rolls.each { |value| count[value] += 1 }

puts "Roll statistics:"
puts "Total rolls: #{rolls_generated}"
puts "File size: #{dice_rolls.size} bits"

(1..MAX_FACE).each do |face|
  percentage = (count[face].to_f / rolls_generated) * 100
  puts "Face #{face}: #{count[face]} times (#{percentage.round(2)}%)"
end

puts "\nFile 'raw_entropy.bin' generated successfully!"

#hashing

key = 'faasd08ddslx002385xoo'
hmac = OpenSSL::HMAC.hexdigest('SHA256', key, File.read('raw_entropy.bin'))
File.write('hmac.txt', hmac)
puts "Hmac: #{hmac}"

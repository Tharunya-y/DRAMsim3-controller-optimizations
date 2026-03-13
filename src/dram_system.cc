#include "dram_system.h"
#include <limits>
#include <assert.h>

namespace dramsim3 {

// alternative way is to assign the id in constructor but this is less
// destructive
int BaseDRAMSystem::total_channels_ = 0;

BaseDRAMSystem::BaseDRAMSystem(Config &config, const std::string &output_dir,
                               std::function<void(uint64_t)> read_callback,
                               std::function<void(uint64_t)> write_callback)
    : read_callback_(read_callback),
      write_callback_(write_callback),
      last_req_clk_(0),
      config_(config),
      timing_(config_),
#ifdef THERMAL
      thermal_calc_(config_),
#endif  // THERMAL
      clk_(0) {
    total_channels_ += config_.channels;

#ifdef ADDR_TRACE
    std::string addr_trace_name = config_.output_prefix + "addr.trace";
    address_trace_.open(addr_trace_name);
#endif
}

int BaseDRAMSystem::GetChannel(uint64_t hex_addr) const {
    hex_addr >>= config_.shift_bits;
    return (hex_addr >> config_.ch_pos) & config_.ch_mask;
}

void BaseDRAMSystem::PrintEpochStats() {
    // first epoch, print bracket
    if (clk_ - config_.epoch_period == 0) {
        std::ofstream epoch_out(config_.json_epoch_name, std::ofstream::out);
        epoch_out << "[";
    }
    for (size_t i = 0; i < ctrls_.size(); i++) {
        ctrls_[i]->PrintEpochStats();
        std::ofstream epoch_out(config_.json_epoch_name, std::ofstream::app);
        epoch_out << "," << std::endl;
    }
#ifdef THERMAL
    thermal_calc_.PrintTransPT(clk_);
#endif  // THERMAL
    return;
}

void BaseDRAMSystem::PrintStats() {
    // Finish epoch output, remove last comma and append ]
    std::ofstream epoch_out(config_.json_epoch_name, std::ios_base::in |
                                                         std::ios_base::out |
                                                         std::ios_base::ate);
    epoch_out.seekp(-2, std::ios_base::cur);
    epoch_out.write("]", 1);
    epoch_out.close();

    std::ofstream json_out(config_.json_stats_name, std::ofstream::out);
    json_out << "{";

    // close it now so that each channel can handle it
    json_out.close();
    for (size_t i = 0; i < ctrls_.size(); i++) {
        ctrls_[i]->PrintFinalStats();
        if (i != ctrls_.size() - 1) {
            std::ofstream chan_out(config_.json_stats_name, std::ofstream::app);
            chan_out << "," << std::endl;
        }
    }
    json_out.open(config_.json_stats_name, std::ofstream::app);
    json_out << "}";

#ifdef THERMAL
    thermal_calc_.PrintFinalPT(clk_);
#endif  // THERMAL
}

void BaseDRAMSystem::ResetStats() {
    for (size_t i = 0; i < ctrls_.size(); i++) {
        ctrls_[i]->ResetStats();
    }
}

void BaseDRAMSystem::RegisterCallbacks(
    std::function<void(uint64_t)> read_callback,
    std::function<void(uint64_t)> write_callback) {
    // TODO this should be propagated to controllers
    read_callback_ = read_callback;
    write_callback_ = write_callback;
}

JedecDRAMSystem::JedecDRAMSystem(Config &config, const std::string &output_dir,
                                 std::function<void(uint64_t)> read_callback,
                                 std::function<void(uint64_t)> write_callback)
    : BaseDRAMSystem(config, output_dir, read_callback, write_callback) {
    if (config_.IsHMC()) {
        std::cerr << "Initialized a memory system with an HMC config file!"
                  << std::endl;
        AbruptExit(__FILE__, __LINE__);
    }

    ctrls_.reserve(config_.channels);
    for (auto i = 0; i < config_.channels; i++) {
#ifdef THERMAL
        ctrls_.push_back(new Controller(i, config_, timing_, thermal_calc_));
#else
        ctrls_.push_back(new Controller(i, config_, timing_));
#endif  // THERMAL
    }
}

JedecDRAMSystem::~JedecDRAMSystem() {
    for (auto it = ctrls_.begin(); it != ctrls_.end(); it++) {
        delete (*it);
    }
}
//CA-1 CODE HERE
int JedecDRAMSystem::PickChannelCA1(uint64_t &addr_inout, bool is_write) const {
    int base_ch = GetChannel(addr_inout);

    // off / trivial cases
    if (!config_.ca1_enable || config_.channels <= 1 || config_.ca1_xor_mask == 0) {
        return base_ch;
    }

    // Compute queue imbalance (use total queue so it works for mixed R/W)
    size_t min_q = std::numeric_limits<size_t>::max();
    size_t max_q = 0;
    for (int ch = 0; ch < config_.channels; ch++) {
        size_t q = ctrls_[ch]->PendingTotalCount();
        min_q = std::min(min_q, q);
        max_q = std::max(max_q, q);
    }

    if ((int)(max_q - min_q) < config_.ca1_imbalance_thresh) {
        return base_ch;
    }

    // Candidate remap (simple XOR hashing)
    uint64_t alt_addr = addr_inout ^ config_.ca1_xor_mask;
    int alt_ch = GetChannel(alt_addr);

    if (alt_ch == base_ch) return base_ch;

    size_t q_base = ctrls_[base_ch]->PendingTotalCount();
    size_t q_alt  = ctrls_[alt_ch]->PendingTotalCount();

    // Switch only if it meaningfully reduces load (prevents oscillation)
    if (q_alt + 1 < q_base) {
        addr_inout = alt_addr;   // IMPORTANT: keep address consistent downstream
        return alt_ch;
    }


    return base_ch;
}
//CA-2 CODE HERE
int JedecDRAMSystem::PickChannelCA2(uint64_t &addr_inout, bool is_write) const {
    int base_ch = GetChannel(addr_inout);

    // off / trivial cases
    if (!config_.ca2_enable || config_.channels <= 1) {
        return PickChannelCA1(addr_inout, is_write);  // fall back to CA1
    }

    // Compute queue imbalance first (same idea as CA1)
    size_t min_q = std::numeric_limits<size_t>::max();
    size_t max_q = 0;
    for (int ch = 0; ch < config_.channels; ch++) {
        size_t q = ctrls_[ch]->PendingTotalCount();
        min_q = std::min(min_q, q);
        max_q = std::max(max_q, q);
    }

    // Reuse CA1 threshold (so behavior stays conservative)
    if (!config_.ca1_enable || config_.ca1_xor_mask == 0 ||
        (int)(max_q - min_q) < config_.ca1_imbalance_thresh) {
        return base_ch;
    }

    // CA2: try multiple XOR masks and choose the least-loaded channel
    // Hardcode a small set that you already tested (good for report reproducibility)
    static const uint64_t masks_all[] = {0x800, 0x1000, 0x2000, 0x4000};

    int best_ch = base_ch;
    uint64_t best_addr = addr_inout;
    size_t best_q = ctrls_[base_ch]->PendingTotalCount();

    int k = std::max(1, std::min(config_.ca2_k, (int)(sizeof(masks_all)/sizeof(masks_all[0]))));

    for (int i = 0; i < k; i++) {
        uint64_t m = masks_all[i];
        uint64_t cand_addr = addr_inout ^ m;
        int cand_ch = GetChannel(cand_addr);
        if (cand_ch == base_ch) continue;

        size_t cand_q = ctrls_[cand_ch]->PendingTotalCount();

        // Switch only if candidate is meaningfully better (hysteresis)
        if (cand_q + (size_t)config_.ca2_delta < best_q) {
            best_q = cand_q;
            best_ch = cand_ch;
            best_addr = cand_addr;
        }
    }

    if (best_ch != base_ch) {
        addr_inout = best_addr;   // keep downstream consistent
    }
    return best_ch;
}
bool JedecDRAMSystem::WillAcceptTransaction(uint64_t hex_addr,
                                            bool is_write) const {
    uint64_t addr = hex_addr;
    int channel = PickChannelCA2(addr, is_write);
    return ctrls_[channel]->WillAcceptTransaction(addr, is_write);
}
bool JedecDRAMSystem::AddTransaction(uint64_t hex_addr, bool is_write) {
#ifdef ADDR_TRACE
    address_trace_ << std::hex << hex_addr << std::dec << " "
                   << (is_write ? "WRITE " : "READ ") << clk_ << std::endl;
#endif

    uint64_t addr = hex_addr;
    int channel = PickChannelCA1(addr, is_write);

    bool ok = ctrls_[channel]->WillAcceptTransaction(addr, is_write);
    assert(ok);
    if (ok) {
        Transaction trans = Transaction(addr, is_write);  // USE remapped addr
        ctrls_[channel]->AddTransaction(trans);
    }
    last_req_clk_ = clk_;
    return ok;
}

void JedecDRAMSystem::ClockTick() {
    for (size_t i = 0; i < ctrls_.size(); i++) {
        // look ahead and return earlier
        while (true) {
            auto pair = ctrls_[i]->ReturnDoneTrans(clk_);
            if (pair.second == 1) {
                write_callback_(pair.first);
            } else if (pair.second == 0) {
                read_callback_(pair.first);
            } else {
                break;
            }
        }
    }
    for (size_t i = 0; i < ctrls_.size(); i++) {
        ctrls_[i]->ClockTick();
    }
    clk_++;

    if (clk_ % config_.epoch_period == 0) {
        PrintEpochStats();
    }
    return;
}

IdealDRAMSystem::IdealDRAMSystem(Config &config, const std::string &output_dir,
                                 std::function<void(uint64_t)> read_callback,
                                 std::function<void(uint64_t)> write_callback)
    : BaseDRAMSystem(config, output_dir, read_callback, write_callback),
      latency_(config_.ideal_memory_latency) {}

IdealDRAMSystem::~IdealDRAMSystem() {}

bool IdealDRAMSystem::AddTransaction(uint64_t hex_addr, bool is_write) {
    auto trans = Transaction(hex_addr, is_write);
    trans.added_cycle = clk_;
    infinite_buffer_q_.push_back(trans);
    return true;
}

void IdealDRAMSystem::ClockTick() {
    for (auto trans_it = infinite_buffer_q_.begin();
         trans_it != infinite_buffer_q_.end();) {
        if (clk_ - trans_it->added_cycle >= static_cast<uint64_t>(latency_)) {
            if (trans_it->is_write) {
                write_callback_(trans_it->addr);
            } else {
                read_callback_(trans_it->addr);
            }
            trans_it = infinite_buffer_q_.erase(trans_it++);
        }
        if (trans_it != infinite_buffer_q_.end()) {
            ++trans_it;
        }
    }

    clk_++;
    return;
}

}  // namespace dramsim3

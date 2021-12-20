"""Prototype for the next version of the H=N marketplace contract.

This version corrects several small bugs from the v2 marketplace contract and
adds the possibility to trade different kinds of FA2 tokens.

"""

import smartpy as sp


class Marketplace(sp.Contract):
    """This contract implements the next version of the H=N marketplace
    contract.

    """

    SWAP_TYPE = sp.TRecord(
        # The user that created the swap
        issuer=sp.TAddress,
        # The token FA2 contract address
        fa2=sp.TAddress,
        # The token id (not necessarily from a OBJKT)
        objkt_id=sp.TNat,
        # The number of swapped editions
        objkt_amount=sp.TNat,
        # The price of each edition in mutez
        xtz_per_objkt=sp.TMutez,
        # The artists royalties in (1000 is 100%)
        royalties=sp.TNat,
        # The address that will receive the royalties
        creator=sp.TAddress).layout(
            ("issuer", ("fa2", ("objkt_id", ("objkt_amount", ("xtz_per_objkt", ("royalties", "creator")))))))

    def __init__(self, manager, metadata, allowed_fa2s, fee):
        """Initializes the contract.

        """
        # Define the contract storage data types for clarity
        self.init_type(sp.TRecord(
            manager=sp.TAddress,
            metadata=sp.TBigMap(sp.TString, sp.TBytes),
            allowed_fa2s=sp.TBigMap(sp.TAddress, sp.TBool),
            swaps=sp.TBigMap(sp.TNat, Marketplace.SWAP_TYPE),
            fee=sp.TNat,
            fee_recipient=sp.TAddress,
            counter=sp.TNat,
            proposed_manager=sp.TOption(sp.TAddress),
            swaps_paused=sp.TBool,
            collects_paused=sp.TBool))

        # Initialize the contract storage
        self.init(
            manager=manager,
            metadata=metadata,
            allowed_fa2s=allowed_fa2s,
            swaps=sp.big_map(),
            fee=fee,
            fee_recipient=manager,
            counter=0,
            proposed_manager=sp.none,
            swaps_paused=False,
            collects_paused=False)

    def check_is_manager(self):
        """Checks that the address that called the entry point is the contract
        manager.

        """
        sp.verify(sp.sender == self.data.manager,
                  message="This can only be executed by the manager")

    def check_no_tez_transfer(self):
        """Checks that no tez were transferred in the operation.

        """
        sp.verify(sp.amount == sp.tez(0),
                  message="The operation does not need tez transfers")

    @sp.entry_point
    def swap(self, params):
        """Swaps several editions of a token for a fixed price.

        """
        # Define the input parameter data type
        sp.set_type(params, sp.TRecord(
            fa2=sp.TAddress,
            objkt_id=sp.TNat,
            objkt_amount=sp.TNat,
            xtz_per_objkt=sp.TMutez,
            royalties=sp.TNat,
            creator=sp.TAddress).layout(
                ("fa2", ("objkt_id", ("objkt_amount", ("xtz_per_objkt", ("royalties", "creator")))))))

        # Check that swaps are not paused
        sp.verify(~self.data.swaps_paused, message="Swaps are paused")

        # Check that no tez have been transferred
        self.check_no_tez_transfer()

        # Check that the token is one of the allowed tokens to trade
        sp.verify(self.data.allowed_fa2s.get(params.fa2, default_value=False),
                  message="This token type cannot be traded")

        # Check that at least one edition will be swapped
        sp.verify(params.objkt_amount > 0,
                  message="At least one edition needs to be swapped")

        # Check that the royalties are within the expected limits
        sp.verify(params.royalties <= 250,
                  message="The royalties cannot be higher than 25%")

        # Transfer all the editions to the marketplace account
        self.fa2_transfer(
            fa2=params.fa2,
            from_=sp.sender,
            to_=sp.self_address,
            token_id=params.objkt_id,
            token_amount=params.objkt_amount)

        # Update the swaps bigmap with the new swap information
        self.data.swaps[self.data.counter] = sp.record(
            issuer=sp.sender,
            fa2=params.fa2,
            objkt_id=params.objkt_id,
            objkt_amount=params.objkt_amount,
            xtz_per_objkt=params.xtz_per_objkt,
            royalties=params.royalties,
            creator=params.creator)

        # Increase the swaps counter
        self.data.counter += 1

    @sp.entry_point
    def collect(self, swap_id):
        """Collects one edition of a token that has already been swapped.

        """
        # Define the input parameter data type
        sp.set_type(swap_id, sp.TNat)

        # Check that collects are not paused
        sp.verify(~self.data.collects_paused, message="Collects are paused")

        # Check that the swap id is present in the swaps big map
        sp.verify(self.data.swaps.contains(swap_id),
                  message="The provided swap_id doesn't exist")

        # Check that the collector is not the creator of the swap
        swap = self.data.swaps[swap_id]
        sp.verify(sp.sender != swap.issuer,
                  message="The collector cannot be the swap issuer")

        # Check that the provided tez amount is exactly the edition price
        sp.verify(sp.amount == swap.xtz_per_objkt,
                  message="The sent tez amount does not coincide with the edition price")

        # Check that there is at least one edition available to collect
        sp.verify(swap.objkt_amount > 0,
                  message="All editions have already been collected")

        # Handle tez tranfers if the edition price is not zero
        sp.if swap.xtz_per_objkt != sp.tez(0):
            # Send the royalties to the NFT creator
            royalties_amount = sp.local(
                "royalties_amount", sp.split_tokens(swap.xtz_per_objkt, swap.royalties, 1000))

            sp.if royalties_amount.value > sp.mutez(0):
                sp.send(swap.creator, royalties_amount.value)

            # Send the management fees
            fee_amount = sp.local(
                "fee_amount", sp.split_tokens(swap.xtz_per_objkt, self.data.fee, 1000))

            sp.if fee_amount.value > sp.mutez(0):
                sp.send(self.data.fee_recipient, fee_amount.value)

            # Send what is left to the swap issuer
            sp.send(swap.issuer, sp.amount - royalties_amount.value - fee_amount.value)

        # Transfer the token edition to the collector
        self.fa2_transfer(
            fa2=swap.fa2,
            from_=sp.self_address,
            to_=sp.sender,
            token_id=swap.objkt_id,
            token_amount=1)

        # Update the number of editions available in the swaps big map
        swap.objkt_amount = sp.as_nat(swap.objkt_amount - 1)

    @sp.entry_point
    def cancel_swap(self, swap_id):
        """Cancels an existing swap.

        """
        # Define the input parameter data type
        sp.set_type(swap_id, sp.TNat)

        # Check that no tez have been transferred
        self.check_no_tez_transfer()

        # Check that the swap id is present in the swaps big map
        sp.verify(self.data.swaps.contains(swap_id),
                  message="The provided swap_id doesn't exist")

        # Check that the swap issuer is cancelling the swap
        swap = self.data.swaps[swap_id]
        sp.verify(sp.sender == swap.issuer,
                  message="Only the swap issuer can cancel the swap")

        # Check that there is at least one swapped edition
        sp.verify(swap.objkt_amount > 0,
                  message="All editions have been collected")

        # Transfer the remaining token editions back to the owner
        self.fa2_transfer(
            fa2=swap.fa2,
            from_=sp.self_address,
            to_=sp.sender,
            token_id=swap.objkt_id,
            token_amount=swap.objkt_amount)

        # Delete the swap entry in the the swaps big map
        del self.data.swaps[swap_id]

    @sp.entry_point
    def update_fee(self, new_fee):
        """Updates the marketplace management fees.

        """
        # Define the input parameter data type
        sp.set_type(new_fee, sp.TNat)

        # Check that the manager executed the entry point
        self.check_is_manager()

        # Check that no tez have been transferred
        self.check_no_tez_transfer()

        # Check that the new fee is not larger than 25%
        sp.verify(new_fee <= 250,
                  message="The management fee cannot be higher than 25%")

        # Set the new management fee
        self.data.fee = new_fee

    @sp.entry_point
    def update_fee_recipient(self, new_fee_recipient):
        """Updates the marketplace management fee recipient address.

        """
        # Define the input parameter data type
        sp.set_type(new_fee_recipient, sp.TAddress)

        # Check that the manager executed the entry point
        self.check_is_manager()

        # Check that no tez have been transferred
        self.check_no_tez_transfer()

        # Set the new management fee recipient address
        self.data.fee_recipient = new_fee_recipient

    @sp.entry_point
    def transfer_manager(self, proposed_manager):
        """Proposes to transfer the marketplace manager to another address.

        """
        # Define the input parameter data type
        sp.set_type(proposed_manager, sp.TAddress)

        # Check that the manager executed the entry point
        self.check_is_manager()

        # Check that no tez have been transferred
        self.check_no_tez_transfer()

        # Set the new proposed manager address
        self.data.proposed_manager = sp.some(proposed_manager)

    @sp.entry_point
    def accept_manager(self):
        """The proposed manager accepts the marketplace manager
        responsabilities.

        """
        # Check that there is a proposed manager
        sp.verify(self.data.proposed_manager.is_some(),
                  message="No new manager has been proposed")

        # Check that the proposed manager executed the entry point
        sp.verify(sp.sender == self.data.proposed_manager.open_some(),
                  message="This can only be executed by the proposed manager")

        # Check that no tez have been transferred
        self.check_no_tez_transfer()

        # Set the new manager address
        self.data.manager = sp.sender

        # Reset the proposed manager value
        self.data.proposed_manager = sp.none

    @sp.entry_point
    def update_metadata(self, params):
        """Updates the contract metadata.

        """
        # Define the input parameter data type
        sp.set_type(params, sp.TRecord(
            key=sp.TString,
            value=sp.TBytes).layout(("key", "value")))

        # Check that the manager executed the entry point
        self.check_is_manager()

        # Check that no tez have been transferred
        self.check_no_tez_transfer()

        # Update the contract metadata
        self.data.metadata[params.key] = params.value

    @sp.entry_point
    def add_fa2(self, fa2):
        """Adds a new FA2 token address to the list of tradable tokens.

        """
        # Define the input parameter data type
        sp.set_type(fa2, sp.TAddress)

        # Check that the manager executed the entry point
        self.check_is_manager()

        # Check that no tez have been transferred
        self.check_no_tez_transfer()

        # Add the new FA2 token address
        self.data.allowed_fa2s[fa2] = True

    @sp.entry_point
    def remove_fa2(self, fa2):
        """Removes one of the tradable FA2 token address.

        """
        # Define the input parameter data type
        sp.set_type(fa2, sp.TAddress)

        # Check that the manager executed the entry point
        self.check_is_manager()

        # Check that no tez have been transferred
        self.check_no_tez_transfer()

        # Disable the FA2 token address
        self.data.allowed_fa2s[fa2] = False

    @sp.entry_point
    def pause_swaps(self, pause):
        """Pause or not the swaps.

        """
        # Define the input parameter data type
        sp.set_type(pause, sp.TBool)

        # Check that the manager executed the entry point
        self.check_is_manager()

        # Check that no tez have been transferred
        self.check_no_tez_transfer()

        # Pause or unpause the swaps
        self.data.swaps_paused = pause

    @sp.entry_point
    def pause_collects(self, pause):
        """Pause or not the collects.

        """
        # Define the input parameter data type
        sp.set_type(pause, sp.TBool)

        # Check that the manager executed the entry point
        self.check_is_manager()

        # Check that no tez have been transferred
        self.check_no_tez_transfer()

        # Pause or unpause the collects
        self.data.collects_paused = pause

    @sp.onchain_view()
    def get_manager(self):
        """Returns the marketplace manager address.

        """
        sp.result(self.data.manager)

    @sp.onchain_view()
    def is_allowed_fa2(self, fa2):
        """Checks if a given FA2 token contract can be traded in the
        marketplace.

        """
        # Define the input parameter data type
        sp.set_type(fa2, sp.TAddress)

        # Return if it can be traded or not
        sp.result(self.data.allowed_fa2s.get(fa2, default_value=False))

    @sp.onchain_view()
    def has_swap(self, swap_id):
        """Check if a given swap id is present in the swaps big map.

        """
        # Define the input parameter data type
        sp.set_type(swap_id, sp.TNat)

        # Return True if the swap id is present in the swaps big map
        sp.result(self.data.swaps.contains(swap_id))

    @sp.onchain_view()
    def get_swap(self, swap_id):
        """Returns the complete information from a given swap id.

        """
        # Define the input parameter data type
        sp.set_type(swap_id, sp.TNat)

        # Check that the swap id is present in the swaps big map
        sp.verify(self.data.swaps.contains(swap_id),
                  message="The provided swap_id doesn't exist")

        # Return the swap information
        sp.result(self.data.swaps[swap_id])

    @sp.onchain_view()
    def get_swaps_counter(self):
        """Returns the swaps counter.

        """
        sp.result(self.data.counter)

    @sp.onchain_view()
    def get_fee(self):
        """Returns the marketplace fee.

        """
        sp.result(self.data.fee)

    @sp.onchain_view()
    def get_fee_recipient(self):
        """Returns the marketplace fee recipient address.

        """
        sp.result(self.data.fee_recipient)

    def fa2_transfer(self, fa2, from_, to_, token_id, token_amount):
        """Transfers a number of editions of a FA2 token between two addresses.

        """
        # Get a handle to the FA2 token transfer entry point
        c = sp.contract(
            t=sp.TList(sp.TRecord(
                from_=sp.TAddress,
                txs=sp.TList(sp.TRecord(
                    to_=sp.TAddress,
                    token_id=sp.TNat,
                    amount=sp.TNat).layout(("to_", ("token_id", "amount")))))),
            address=fa2,
            entry_point="transfer").open_some()

        # Transfer the FA2 token editions to the new address
        sp.transfer(
            arg=sp.list([sp.record(
                from_=from_,
                txs=sp.list([sp.record(
                    to_=to_,
                    token_id=token_id,
                    amount=token_amount)]))]),
            amount=sp.mutez(0),
            destination=c)


# Add a compilation target initialized to a test account and the OBJKT FA2 contract
sp.add_compilation_target("marketplace", Marketplace(
    manager=sp.address("tz1gnL9CeM5h5kRzWZztFYLypCNnVQZjndBN"),
    metadata=sp.utils.metadata_of_url("ipfs://aaa"),
    allowed_fa2s=sp.big_map({sp.address("KT1RJ6PbjHpwc3M5rw5s2Nbmefwbuwbdxton"): True}),
    fee=sp.nat(25)))
